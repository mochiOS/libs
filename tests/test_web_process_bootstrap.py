"""Exercise the actual native entry point with signed-resource API doubles."""
from pathlib import Path
import json
import shutil
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]

@unittest.skipUnless(shutil.which('c++'),'Host C++ required')
class NativeWebEntry(unittest.TestCase):
    def test_own_view_lifetime_rejection_and_argument_forwarding(self):
        contract=json.loads((ROOT/'engines/webkit/port.json').read_text())['font_resource_export']
        with tempfile.TemporaryDirectory() as t:
            d=Path(t);obj=d/'entry.o';binary=d/'test'
            subprocess.run(['c++','-std=c++20','-Wall','-Wextra','-Werror','-Dmain=native_web_main',
                            '-DMWK_FONT_RESOURCE_EXPORT="'+contract+'"','-c',str(ROOT/'engines/webkit/src/web_process_main.cpp'),'-o',str(obj)],check=True)
            harness=d/'test.cpp';harness.write_text(r'''
#include <cassert>
#include <cerrno>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>
extern int native_web_main(int,char**);
static int mode,closed,entered;
static char name[]="WPEWebProcess";
static char* arguments[]={name,nullptr};
extern "C" uint64_t mochi_bundle_directory_open(const char* alias,char* path,size_t size) {
 assert(!*alias && size==128);
 if(mode==1)return 0;
 if(mode==2){std::memset(path,'x',size);return 7;}
 std::strcpy(path,mode==3?"/tmp/untrusted":"/.__mochi_resources/17");return 7;
}
extern "C" int mochi_bundle_directory_close(uint64_t token) { assert(token==7);++closed;return 0; }
extern "C" int __real_setenv(const char*,const char*,int);
extern "C" int __wrap_setenv(const char* key,const char* value,int replace) {
 if(mode==4){errno=ENOMEM;return -1;}return __real_setenv(key,value,replace);
}
namespace WebKit {
int WebProcessMain(int argc,char** argv) {
 assert(argc==1 && argv==arguments && !closed);++entered;
 std::string root="/.__mochi_resources/17/" MWK_FONT_RESOURCE_EXPORT;
 assert(std::getenv("FONTCONFIG_FILE")==root+"/fonts.conf");
 assert(std::getenv("FONTCONFIG_PATH")==root);
 return 29;
}
}
int main() {
 assert(native_web_main(1,arguments)==29 && closed==1 && entered==1);
 for(mode=1;mode<=4;++mode){closed=entered=0;assert(native_web_main(1,arguments)==125);assert(!entered);assert(closed==(mode!=1));}
}
''')
            subprocess.run(['c++','-std=c++20','-Wall','-Wextra','-Werror','-DMWK_FONT_RESOURCE_EXPORT="'+contract+'"',str(harness),str(obj),'-Wl,--wrap=setenv','-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)
