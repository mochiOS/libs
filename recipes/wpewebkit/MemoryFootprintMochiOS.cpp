// mochiOS native process accounting. Linux procfs is not part of this ABI.
#include "config.h"
#include <mochios/process.h>
#include <wtf/Assertions.h>
#include <wtf/MemoryFootprint.h>

namespace WTF {
size_t memoryFootprint()
{
    struct mochios_process_memory_usage usage;
    // A missing kernel/SDK contract must not be reported as zero usage.
    RELEASE_ASSERT(!mochios_process_memory_read(&usage, sizeof(usage)));
    RELEASE_ASSERT(usage.current_bytes <= usage.peak_bytes);
    return usage.current_bytes;
}
} // namespace WTF
