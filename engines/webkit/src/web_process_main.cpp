// The signed executable's own resource selection supplies all font data.
// A parent process's directory path is deliberately never reused here.
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#ifndef MWK_FONT_RESOURCE_EXPORT
#error The font resource contract must be supplied by the reviewed port definition.
#endif

extern "C" uint64_t mochi_bundle_directory_open(const char*, char*, size_t);
extern "C" int mochi_bundle_directory_close(uint64_t);
namespace WebKit { int WebProcessMain(int, char**); }

namespace {
struct Resources {
    uint64_t token { 0 };
    ~Resources() { if (token) mochi_bundle_directory_close(token); }
};
constexpr int startupFailure = 125;
}

int main(int argc, char** argv)
{
    Resources resources;
    char root[128] {};
    resources.token = mochi_bundle_directory_open("", root, sizeof(root));
    if (!resources.token)
        return startupFailure;
    constexpr char prefix[] = "/.__mochi_resources/";
    if (!std::memchr(root, '\0', sizeof(root))
        || std::strncmp(root, prefix, sizeof(prefix) - 1) || std::strstr(root, ".."))
        return startupFailure;
    char configuration[256];
    char directory[256];
    int configLength = std::snprintf(configuration, sizeof(configuration), "%s/" MWK_FONT_RESOURCE_EXPORT "/fonts.conf", root);
    int directoryLength = std::snprintf(directory, sizeof(directory), "%s/" MWK_FONT_RESOURCE_EXPORT, root);
    if (configLength < 0 || static_cast<size_t>(configLength) >= sizeof(configuration)
        || directoryLength < 0 || static_cast<size_t>(directoryLength) >= sizeof(directory)
        || setenv("FONTCONFIG_FILE", configuration, 1) || setenv("FONTCONFIG_PATH", directory, 1))
        return startupFailure;
    return WebKit::WebProcessMain(argc, argv);
}
