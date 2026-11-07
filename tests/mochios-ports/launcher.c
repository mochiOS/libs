#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
int main(void) {
    char *argv[]={"port-smoke",NULL};
    if(!setenv("LD_LIBRARY_PATH","/system/libraries/port-smoke",1))execv("/system/bin/port-smoke",argv);
    freopen("/tmp/port-smoke-results.log","a",stderr);
    perror("PORT-SMOKE FAIL launcher");return 1;
}
