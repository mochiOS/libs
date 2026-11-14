// Keep executable-owned libc in sync with the adapter SDK. The complete
// network process implementation remains in the authenticated WPE library.
namespace WebKit { int NetworkProcessMain(int, char**); }

int main(int argc, char** argv)
{
    return WebKit::NetworkProcessMain(argc, argv);
}
