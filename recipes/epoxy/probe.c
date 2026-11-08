/* Real surfaceless EGL/GLES2 pixels; no display/window system or fake driver. */
#include <epoxy/egl.h>
#include <epoxy/gl.h>
#include <stdio.h>
#include <stdlib.h>
int main(void) {
    EGLDisplay display=EGL_NO_DISPLAY;
    EGLSurface surface=EGL_NO_SURFACE;
    EGLContext context=EGL_NO_CONTEXT;
    const char *stage="surfaceless display";
    int result=1;
    if(!epoxy_has_egl()){puts("EPOXY EGL FAIL missing provider");return 1;}
    if(!epoxy_extension_in_string("GL_EXT_one GL_EXT_two","GL_EXT_one") ||
       epoxy_extension_in_string("GL_EXT_one GL_EXT_two","GL_EXT_on"))return 2;
    /* Before a display is initialized Epoxy conservatively assumes EGL 1.4.
     * Bootstrap via the advertised client extension, not a guessed EGL 1.5. */
    if(!epoxy_has_egl_extension(EGL_NO_DISPLAY,"EGL_EXT_platform_base"))goto done;
    display=eglGetPlatformDisplayEXT(EGL_PLATFORM_SURFACELESS_MESA,EGL_DEFAULT_DISPLAY,NULL);
    if(display==EGL_NO_DISPLAY)goto done;
    stage="initialize"; if(!eglInitialize(display,NULL,NULL))goto done;
    stage="bind GLES";if(!eglBindAPI(EGL_OPENGL_ES_API))goto done;
    EGLConfig config;EGLint count=0;
    const EGLint config_attributes[]={EGL_SURFACE_TYPE,EGL_PBUFFER_BIT,EGL_RENDERABLE_TYPE,EGL_OPENGL_ES2_BIT,EGL_RED_SIZE,8,EGL_GREEN_SIZE,8,EGL_BLUE_SIZE,8,EGL_ALPHA_SIZE,8,EGL_NONE};
    stage="choose config";if(!eglChooseConfig(display,config_attributes,&config,1,&count)||count<1)goto done;
    const EGLint surface_attributes[]={EGL_WIDTH,4,EGL_HEIGHT,4,EGL_NONE};
    stage="create pbuffer";surface=eglCreatePbufferSurface(display,config,surface_attributes);if(surface==EGL_NO_SURFACE)goto done;
    const EGLint context_attributes[]={EGL_CONTEXT_CLIENT_VERSION,2,EGL_NONE};
    stage="create GLES2 context";context=eglCreateContext(display,config,EGL_NO_CONTEXT,context_attributes);if(context==EGL_NO_CONTEXT)goto done;
    stage="make current";if(!eglMakeCurrent(display,surface,surface,context))goto done;
    stage="dispatch metadata";
    if(epoxy_is_desktop_gl() || epoxy_gl_version()<20)goto done;
    if(epoxy_has_gl_extension("GL_MOCHIOS_nonexistent_extension"))goto done;
    printf("EPOXY renderer=%s version=%s\n",glGetString(GL_RENDERER),glGetString(GL_VERSION));
    unsigned char pixel[4]={0};
    glClearColor(0.25f,0.5f,0.75f,1.0f);glClear(GL_COLOR_BUFFER_BIT);
    glReadPixels(0,0,1,1,GL_RGBA,GL_UNSIGNED_BYTE,pixel);
    stage="pixel readback";
    if(glGetError()!=GL_NO_ERROR||abs((int)pixel[0]-64)>2||abs((int)pixel[1]-128)>2||abs((int)pixel[2]-191)>2||pixel[3]!=255)goto done;
    result=0;
done:
    if(result)fprintf(stderr,"EPOXY EGL FAIL stage=%s error=0x%x\n",stage,eglGetError());
    if(display!=EGL_NO_DISPLAY){eglMakeCurrent(display,EGL_NO_SURFACE,EGL_NO_SURFACE,EGL_NO_CONTEXT);if(context!=EGL_NO_CONTEXT)eglDestroyContext(display,context);if(surface!=EGL_NO_SURFACE)eglDestroySurface(display,surface);eglTerminate(display);}
    puts(result?"EPOXY EGL FAIL":"EPOXY EGL PASS real software pixel readback");return result;
}
