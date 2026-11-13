"""Exercise the actual patched random-device methods with injected entropy errors."""
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
    for at in range(body, len(source)):
        if source[at] == '{': depth += 1
        elif source[at] == '}':
            depth -= 1
            if not depth: return source[start:at + 1]
    raise ValueError('Incomplete upstream method')


@unittest.skipUnless(shutil.which('c++') and shutil.which('patch'), 'Host C++ and patch required')
class NativeRandomSource(unittest.TestCase):
    def test_native_entropy_chunking_interrupts_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            source = work / 'Source/WTF/wtf/RandomDevice.cpp'
            source.parent.mkdir(parents=True)
            shutil.copy2(ROOT / 'tests/fixtures/wpe-2.54.2/Source/WTF/wtf/RandomDevice.cpp', source)
            result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-d', str(work),
                                     '-i', str(ROOT / 'recipes/wpewebkit/random-source.patch')],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            text = source.read_text()
            methods = '\n'.join(method(text, signature) for signature in (
                'RandomDevice::RandomDevice()', 'RandomDevice::~RandomDevice()',
                'void RandomDevice::cryptographicallyRandomValues('))
            harness = r'''
#include <algorithm>
#include <cassert>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <span>
#include <vector>
#define OS(x) OS_##x
#define OS_DARWIN 0
#define OS_FUCHSIA 0
#define OS_WINDOWS 0
#define OS_UNIX 1
#define OS_MOCHIOS 1
#define WTF_ALLOW_UNSAFE_BUFFER_USAGE_BEGIN
#define WTF_ALLOW_UNSAFE_BUFFER_USAGE_END
struct Rejected {};
#define RELEASE_ASSERT(value) do { if (!(value)) throw Rejected {}; } while (false)
static unsigned calls, interrupts, failAt;
static int failure;
static int nativeEntropy(void* data, size_t size) {
    ++calls;
    assert(size && size<=256);
    if (interrupts) { --interrupts;errno=EINTR;return -1; }
    if (calls==failAt) { errno=failure;return -1; }
    std::fill_n(static_cast<uint8_t*>(data),size,0xA7);
    return 0;
}
#define getentropy nativeEntropy
namespace WTF {
class RandomDevice {
public:
    RandomDevice();~RandomDevice();
    void cryptographicallyRandomValues(std::span<uint8_t>);
private:int m_fd {-1};
};
''' + methods + r'''
}
int main() {
    for (size_t size : {0u,1u,255u,256u,257u,511u,512u,513u,8192u}) {
        calls=interrupts=failAt=0;
        std::vector<uint8_t> bytes(size+26,0xCD);
        { WTF::RandomDevice device;device.cryptographicallyRandomValues(std::span(bytes).subspan(13,size)); }
        assert(calls==(size+255)/256);
        assert(std::all_of(bytes.begin(),bytes.begin()+13,[](auto b){return b==0xCD;}));
        assert(std::all_of(bytes.begin()+13,bytes.begin()+13+size,[](auto b){return b==0xA7;}));
        assert(std::all_of(bytes.begin()+13+size,bytes.end(),[](auto b){return b==0xCD;}));
    }
    calls=failAt=0;interrupts=3;
    std::vector<uint8_t> bytes(513,0);
    WTF::RandomDevice device;device.cryptographicallyRandomValues(bytes);
    assert(calls==6 && !interrupts && std::all_of(bytes.begin(),bytes.end(),[](auto b){return b==0xA7;}));
    for (int error : {EACCES,EIO,EAGAIN}) {
        calls=interrupts=0;failAt=1;failure=error;bool rejected=false;
        std::fill(bytes.begin(),bytes.end(),0xCD);
        try { device.cryptographicallyRandomValues(bytes); } catch (Rejected&) { rejected=true; }
        assert(rejected && calls==1 && std::all_of(bytes.begin(),bytes.end(),[](auto b){return b==0xCD;}));
    }
    calls=interrupts=0;failAt=2;failure=EACCES;bool rejected=false;
    std::fill(bytes.begin(),bytes.end(),0xCD);
    try { device.cryptographicallyRandomValues(bytes); } catch (Rejected&) { rejected=true; }
    assert(rejected && calls==2);
    assert(std::all_of(bytes.begin(),bytes.begin()+256,[](auto b){return b==0xA7;}));
    assert(std::all_of(bytes.begin()+256,bytes.end(),[](auto b){return b==0xCD;}));
}
'''
            cpp = work / 'harness.cpp'; cpp.write_text(harness)
            binary = work / 'harness'
            compiled = subprocess.run(['c++', '-std=c++20', '-Wall', '-Wextra', '-Werror',
                                       str(cpp), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__': unittest.main()
