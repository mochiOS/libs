#include <stdio.h>
int port_probe_main(void);
int main(void) {
    if(!freopen("/tmp/port-smoke-results.log","a",stdout)||!freopen("/tmp/port-smoke-results.log","a",stderr))return 2;
    setvbuf(stdout,NULL,_IONBF,0);setvbuf(stderr,NULL,_IONBF,0);
    puts("PORT-SMOKE START");
    int result=port_probe_main();
    printf("PORT-SMOKE COMPLETE result=%d\n",result);
    return result;
}
