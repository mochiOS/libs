"""Exercise the actual patched WPE methods with a host control-flow harness.

The fixture retains upstream notices. Host flock is never used: open, flock,
close and truncate are injected so errors and operation ordering are observable.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def method(source, signature):
    start = source.index(signature)
    body = source.index('{', start)
    depth = 0
    for index in range(body, len(source)):
        if source[index] == '{': depth += 1
        elif source[index] == '}':
            depth -= 1
            if not depth: return source[start:index + 1]
    raise ValueError('Incomplete upstream method')


@unittest.skipUnless(shutil.which('c++') and shutil.which('patch'), 'Host C++ and patch required')
class NativeLockIntegration(unittest.TestCase):
    def test_native_lock_acquisition_and_truncate_preserve_order_and_errors(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            shutil.copytree(ROOT / 'tests/fixtures/wpe-2.54.2', work / 'source')
            prefix = 'Source/WTF/wtf/'
            names = [prefix + 'FileHandle.cpp', prefix + 'posix/FileHandlePOSIX.cpp', prefix + 'posix/FileSystemPOSIX.cpp']
            sections = (ROOT / 'recipes/wpewebkit/mochios.patch').read_text().split('diff --git ')
            selected = ['diff --git ' + s for s in sections[1:]
                        if any(s.startswith('a/' + n + ' b/' + n + '\n') for n in names)]
            self.assertGreaterEqual(len(selected), 2)
            patchfile = work / 'port.patch'
            patchfile.write_text(''.join(selected))
            result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-d', work / 'source',
                                     '-i', patchfile], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            common = (work / 'source' / names[0]).read_text()
            posix = (work / 'source' / names[1]).read_text()
            methods = '\n'.join([
                method(common, 'FileHandle::FileHandle(PlatformFileHandle handle,'),
                method(common, 'FileHandle::~FileHandle()'),
                method(common, 'FileHandle::FileHandle(FileHandle&&'),
                method(common, 'FileHandle& FileHandle::operator=(FileHandle&&'),
                method(posix, 'bool FileHandle::lock('),
                method(posix, 'void FileHandle::close()'),
                method((work / 'source' / names[2]).read_text(), 'FileHandle openFile('),
            ])
            harness = r'''
#include <optional>
#include <utility>
#include <cassert>
#include <cerrno>
#include <cstdint>
#include <fcntl.h>
#include <sys/file.h>
#include <sys/stat.h>
#define OS(x) OS_##x
#define OS_MOCHIOS 1
#define USE(x) USE_##x
#define USE_FILE_LOCK 1
#define UNUSED_PARAM(x) (void)(x)
static int closes, lastClosed, opens, conversions, locks, truncates, openFlags;
static bool lockFailure, truncateFailure;
extern "C" int open(const char*, int flags, ...) { ++opens; openFlags = flags; return 31; }
extern "C" int close(int fd) { ++closes; lastClosed = fd; errno = EBADF; return -1; }
extern "C" int flock(int fd, int flags) {
    assert(fd >= 0 && (flags == 1 || flags == 2 || flags == 5 || flags == 6));
    ++locks;
    if (lockFailure) { errno = EWOULDBLOCK; return -1; }
    return 0;
}
namespace WTF::FileSystemImpl {
using PlatformFileHandle = int;
struct String { const char* text; };
struct CString { const char* text; bool isNull() const { return !text; } const char* data() const { return text; } };
CString fileSystemRepresentation(const String& text) { ++conversions; return {text.text}; }
enum class FileOpenMode { Read, Truncate, ReadWrite };
enum class FileAccessPermission { User, All };
enum class FileLockMode { Shared = 1, Exclusive = 2, Nonblocking = 4 };
template<typename T> struct OptionSet {
    unsigned bits;
    explicit operator bool() const { return bits; }
    unsigned toRaw() const { return bits; }
};
class FileHandle {
public:
    FileHandle() = default;
    FileHandle(PlatformFileHandle, OptionSet<FileLockMode>);
    FileHandle(FileHandle&&);
    FileHandle& operator=(FileHandle&&);
    static FileHandle adopt(PlatformFileHandle handle, OptionSet<FileLockMode> mode) { return FileHandle(handle, mode); }
    ~FileHandle();
    bool valid() const { return m_handle.has_value(); }
    explicit operator bool() const { return valid(); }
    bool truncate(int64_t offset) {
        assert(valid() && offset == 0 && !lockFailure && locks > 0);
        ++truncates;
        if (truncateFailure) { errno = ENOSPC; return false; }
        return true;
    }
private:
    bool lock(OptionSet<FileLockMode>);
    void close();
    std::optional<int> m_handle;
};
''' + methods + r'''
}
int main() {
    using namespace WTF::FileSystemImpl;
    { FileHandle handle(0, {0}); assert(handle.valid()); assert(closes == 0); }
    assert(closes == 1 && lastClosed == 0);
    for (bool fail : {false, true}) {
        lockFailure = fail;
        for (unsigned mode : {1u, 2u, 5u, 6u}) {
            int before = closes, beforeLocks = locks;
            errno = 0;
            { FileHandle handle(17, {mode}); assert(handle.valid() != fail);
              assert(locks == beforeLocks + 1);
              if (fail) assert(errno == EWOULDBLOCK && closes == before + 1); }
            assert(closes == before + 1 && lastClosed == 17);
        }
        for (auto mode : {FileOpenMode::Read, FileOpenMode::Truncate, FileOpenMode::ReadWrite}) {
            int before = closes, beforeOpens = opens, beforeLocks = locks, beforeTruncates = truncates;
            errno = 0;
            { auto handle = openFile({"existing-file"}, mode, FileAccessPermission::All, {2}, false);
              assert(handle.valid() != fail && opens == beforeOpens + 1 && locks == beforeLocks + 1);
              assert(!(openFlags & O_TRUNC));
              assert(truncates == beforeTruncates + int(!fail && mode == FileOpenMode::Truncate));
              if (fail) assert(errno == EWOULDBLOCK && closes == before + 1); }
            assert(closes == before + 1);
        }
    }
    lockFailure = false; truncateFailure = true;
    int before = closes;
    { auto handle = openFile({"existing-file"}, FileOpenMode::Truncate, FileAccessPermission::User, {2}, false);
      assert(!handle.valid() && errno == ENOSPC && closes == before + 1); }
    assert(closes == before + 1);
    int beforeLocks = locks, beforeTruncates = truncates;
    { auto handle = openFile({"ordinary-file"}, FileOpenMode::Truncate, FileAccessPermission::User, {0}, false);
      assert(handle.valid() && (openFlags & O_TRUNC)); }
    assert(locks == beforeLocks && truncates == beforeTruncates);
}
'''
            source = work / 'test.cpp'
            source.write_text(harness)
            executable = work / 'test'
            result = subprocess.run(['c++', '-std=c++23', '-Wall', '-Wextra', '-Werror',
                                     source, '-o', executable], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([executable], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
