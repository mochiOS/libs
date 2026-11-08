/* Validate the existing SDK executable-name extension used by WTF's native
 * path adapter. A process-local name is not executable authentication. */
#include <stdio.h>
#include <string.h>
#include <unistd.h>
extern const char *getexecname(void);

int native_executable_name_probe(void)
{
    const char *name = getexecname();
    if (!name || strcmp(name, "/system/bin/port-smoke"))
        return 1;
    char original[4096];
    if (!getcwd(original, sizeof(original)))
        return 2;
    if (chdir("/tmp"))
        return 3;
    const char *after = getexecname();
    int result = !after || strcmp(after, "/system/bin/port-smoke");
    if (chdir(original))
        return 4;
    if (result)
        return 5;
    puts("NATIVE EXECUTABLE NAME PASS stable across cwd changes");
    return 0;
}
