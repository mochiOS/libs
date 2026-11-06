#include <libtasn1.h>
#include <string.h>
int main(void) {
    const unsigned char valid[] = {0x04, 0x03, 'a', 'b', 'c'};
    const unsigned char *value = NULL;
    unsigned int length = 0;
    if (!asn1_check_version(ASN1_VERSION)) return 1;
    if (asn1_decode_simple_der(ASN1_ETYPE_OCTET_STRING, valid, sizeof valid, &value, &length)) return 1;
    if (length != 3 || memcmp(value, "abc", 3)) return 1;
    if (asn1_decode_simple_der(ASN1_ETYPE_OCTET_STRING, valid, sizeof valid - 1, &value, &length) == ASN1_SUCCESS) return 1;
    unsigned char encoded[16] = {0};
    length = sizeof encoded;
    if (asn1_encode_simple_der(ASN1_ETYPE_OCTET_STRING, valid + 2, 3, encoded, &length)) return 1;
    if (length != 2 || encoded[0] != 0x04 || encoded[1] != 0x03) return 1;
    unsigned char long_length[130] = {0x81, 0x80};
    int consumed = 0;
    if (asn1_get_length_der(long_length, sizeof long_length, &consumed) != 128 || consumed != 2) return 1;
    return 0;
}
