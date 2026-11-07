"""Exercise the port's real secure-environment helper, not an unsafe getenv fallback."""
from pathlib import Path
import json
import shutil
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]

class XkbPort(unittest.TestCase):
    def test_recipe_keeps_only_the_target_library_and_explicit_feature_selection(self):
        recipe=json.loads((ROOT/'recipes/xkbcommon/recipe.json').read_text())
        for name in ['enable-tests','enable-tools','enable-x11','enable-wayland','enable-xkbregistry']:
            self.assertIs(recipe['options'][name],False)
        self.assertEqual(recipe['pkg_config_modules'],['xkbcommon'])
        self.assertEqual(recipe['install_tags'],['runtime','devel'])

    @unittest.skipUnless(shutil.which('cc'), 'Host C compiler required')
    def test_secure_mode_missing_auxv_and_all_credential_failures_do_not_read_environment(self):
        text=(ROOT/'recipes/xkbcommon/mochios.patch').read_text()
        added=text.split('+++ b/src/mochios-secure-env.h\n',1)[1]
        header='\n'.join(line[1:] for line in added.splitlines() if line.startswith('+'))+'\n'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'secure.h').write_text(header)
            source=r'''
#include <errno.h>
#include <stdlib.h>
#include <sys/auxv.h>
#include <sys/types.h>
#include <unistd.h>
#include <assert.h>
static unsigned long secure;
static int missing,lookups;
static uid_t uid=1000,euid=1000;
static gid_t gid=1000,egid=1000;
static unsigned long aux(unsigned long key){assert(key==AT_SECURE);if(missing)errno=ENOENT;return secure;}
static uid_t u(void){return uid;} static uid_t eu(void){return euid;}
static gid_t g(void){return gid;} static gid_t eg(void){return egid;}
static char *env(const char *key){assert(key);++lookups;return (char *)"kept";}
#define getauxval aux
#define getuid u
#define geteuid eu
#define getgid g
#define getegid eg
#define getenv env
#include "secure.h"
static void denied(void){int n=lookups;errno=EBUSY;assert(xkb_mochios_secure_getenv("XKB_CONFIG_ROOT")==NULL);assert(lookups==n);assert(errno==EBUSY);}
int main(void){
 errno=EBUSY;assert(xkb_mochios_secure_getenv("XKB_CONFIG_ROOT")!=NULL);assert(lookups==1&&errno==EBUSY);
 secure=1;denied();secure=0;missing=1;denied();missing=0;
 euid=0;denied();euid=uid;egid=0;denied();egid=gid;
 uid=(uid_t)-1;denied();uid=1000;euid=(uid_t)-1;denied();euid=1000;
 gid=(gid_t)-1;denied();gid=1000;egid=(gid_t)-1;denied();egid=1000;
 int n=lookups;assert(!xkb_mochios_secure_getenv(NULL)&&n==lookups);
 return 0;
}
'''
            (root/'test.c').write_text(source)
            subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',root/'test.c','-o',root/'test'],check=True)
            subprocess.run([root/'test'],check=True,timeout=10)

if __name__=='__main__':unittest.main()
