#include <ffi.h>
static int add(int a, int b) { return a + b; }
int main(void) {
    ffi_cif cif;
    ffi_type *types[] = { &ffi_type_sint, &ffi_type_sint };
    int a = 20, b = 22;
    void *values[] = { &a, &b };
    ffi_arg result = 0;
    if (ffi_prep_cif(&cif, FFI_DEFAULT_ABI, 2, &ffi_type_sint, types) != FFI_OK) return 1;
    ffi_call(&cif, FFI_FN(add), &result, values);
    return result != 42;
}
