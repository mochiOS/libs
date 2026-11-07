#include <span>
extern "C" int mochios_webkit_toolchain_probe();
int main() {
    int values[]{mochios_webkit_toolchain_probe()};
    return std::span(values).front() == 42 ? 0 : 1;
}
