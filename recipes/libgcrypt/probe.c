#include <gcrypt.h>
#include <string.h>
static int cryptographic_vectors(void) {
    static const unsigned char sha256[32] = {
        0xba,0x78,0x16,0xbf,0x8f,0x01,0xcf,0xea,0x41,0x41,0x40,0xde,0x5d,0xae,0x22,0x23,
        0xb0,0x03,0x61,0xa3,0x96,0x17,0x7a,0x9c,0xb4,0x10,0xff,0x61,0xf2,0x00,0x15,0xad};
    static const unsigned char ciphertext[16] = {
        0x03,0x88,0xda,0xce,0x60,0xb6,0xa3,0x92,0xf3,0x28,0xc2,0xb9,0x71,0xb2,0xfe,0x78};
    static const unsigned char tag[16] = {
        0xab,0x6e,0x47,0xd4,0x2c,0xec,0x13,0xbd,0xf5,0x3a,0x67,0xb2,0x12,0x57,0xbd,0xdf};
    unsigned char digest[32], key[16] = {0}, iv[12] = {0}, input[16] = {0}, output[16] = {0}, mac[16] = {0};
    gcry_md_hash_buffer(GCRY_MD_SHA256, digest, "abc", 3);
    if (memcmp(digest, sha256, sizeof digest)) return 1;
    gcry_cipher_hd_t cipher;
    if (gcry_cipher_open(&cipher, GCRY_CIPHER_AES128, GCRY_CIPHER_MODE_GCM, 0)) return 1;
    int failed = gcry_cipher_setkey(cipher, key, sizeof key) ||
                 gcry_cipher_setiv(cipher, iv, sizeof iv) ||
                 gcry_cipher_encrypt(cipher, output, sizeof output, input, sizeof input) ||
                 gcry_cipher_gettag(cipher, mac, sizeof mac);
    failed |= memcmp(output, ciphertext, sizeof output) != 0 || memcmp(mac, tag, sizeof mac) != 0;
    gcry_cipher_close(cipher);
    if (failed) return 1;
    if (gcry_cipher_open(&cipher, GCRY_CIPHER_AES128, GCRY_CIPHER_MODE_GCM, 0)) return 1;
    failed = gcry_cipher_setkey(cipher, key, sizeof key) ||
             gcry_cipher_setiv(cipher, iv, sizeof iv) ||
             gcry_cipher_decrypt(cipher, output, sizeof output, ciphertext, sizeof ciphertext) ||
             gcry_cipher_checktag(cipher, tag, sizeof tag);
    failed |= memcmp(output, input, sizeof input) != 0;
    mac[0] ^= 1;
    failed |= gcry_cipher_checktag(cipher, mac, sizeof mac) == 0;
    gcry_cipher_close(cipher);
    return failed;
}
int main(void) {
    if (!gcry_check_version(GCRYPT_VERSION)) return 1;
    if (gcry_control(GCRYCTL_INITIALIZATION_FINISHED, 0)) return 1;
    if (cryptographic_vectors()) return 1;
    /* Native entropy failures remain fatal in the upstream backend. */
    unsigned char random[32];
    gcry_randomize(random, sizeof random, GCRY_VERY_STRONG_RANDOM);
    gcry_fast_random_poll();
    return 0;
}
