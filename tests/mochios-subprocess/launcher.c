#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
int main(void) {
    char *argv[] = {"gsubprocess-smoke", NULL};
    if (!setenv("LD_LIBRARY_PATH", "/system/libraries/gsubprocess-smoke", 1) &&
        !setenv("PATH", "/system/bin", 1)) execv("/system/bin/gsubprocess-smoke", argv);
    freopen("/tmp/gsubprocess-results.log", "a", stderr);
    perror("GSUBPROCESS FAIL launcher exec");
    return 1;
}
