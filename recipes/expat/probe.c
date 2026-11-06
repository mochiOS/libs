#include <expat.h>
#include <string.h>
static void start(void *data, const XML_Char *name, const XML_Char **attributes) {
    unsigned *count = data;
    if (!strcmp(name, "fontconfig") && attributes) ++*count;
}
int main(void) {
    unsigned count = 0;
    XML_Parser parser = XML_ParserCreate(NULL);
    if (!parser) return 1;
    XML_SetUserData(parser, &count);
    XML_SetStartElementHandler(parser, start);
    const char xml[] = "<fontconfig><dir>/system/libraries/fonts</dir></fontconfig>";
    int failed = XML_Parse(parser, xml, sizeof(xml) - 1, XML_TRUE) != XML_STATUS_OK || count != 1;
    XML_ParserFree(parser);
    parser = XML_ParserCreate(NULL);
    if (!parser) return 1;
    const char invalid[] = "<fontconfig></other>";
    failed |= XML_Parse(parser, invalid, sizeof(invalid) - 1, XML_TRUE) != XML_STATUS_ERROR;
    failed |= XML_GetErrorCode(parser) != XML_ERROR_TAG_MISMATCH;
    XML_ParserFree(parser);
    return failed;
}
