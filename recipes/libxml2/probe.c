#include <libxml/parser.h>
#include <libxml/tree.h>
int main(void) {
    const char input[] = "<mochi/>";
    xmlDoc *document = xmlReadMemory(input, sizeof(input)-1, "probe.xml", 0, XML_PARSE_NONET);
    if (!document) return 1;
    xmlNode *node = xmlDocGetRootElement(document);
    int failed = !node || xmlStrcmp(node->name, BAD_CAST "mochi");
    xmlFreeDoc(document);
    return failed;
}
