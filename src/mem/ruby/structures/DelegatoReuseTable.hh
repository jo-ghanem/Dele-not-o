/*
 * DelegatoReuseTable — per-L2 heartbeat table for the Delegato predictor
 * (chiplet.pdf §5.2). Paper-faithful lifecycle: allocate on SnpAMO
 * arrival, mark_reused on local AMO at L2 UC/UD (same-core re-touch since
 * the previous delegate), consume + reset via lookup_and_reset when L2
 * emits the next SnpResp, evict as housekeeping. NOT a generic fill/evict
 * cache — see delegato_fsm.md for the full lifecycle.
 */

#ifndef __MEM_RUBY_STRUCTURES_DELEGATOREUSETABLE_HH__
#define __MEM_RUBY_STRUCTURES_DELEGATOREUSETABLE_HH__

#include <cstdint>
#include <list>
#include <vector>

#include "mem/ruby/common/Address.hh"

namespace gem5 {
namespace ruby {

class DelegatoReuseTable
{
  public:
    DelegatoReuseTable(int num_entries, int num_ways);

    bool has(Addr addr) const;
    void allocate(Addr addr);            // on SnpAMO arrival at owner L2
    void mark_reused(Addr addr);         // on local AMO at L2 UC/UD
    bool lookup_and_reset(Addr addr);    // on SnpResp emission: read+clear
    void evict(Addr addr);               // housekeeping on L2 line dealloc
    int  size() const;

  private:
    struct Entry
    {
        Addr tag = 0;
        bool valid = false;
        bool reuse = false;
    };

    int setIndex(Addr addr) const;
    int findWay(int set_idx, Addr addr) const;
    int pickVictimWay(int set_idx);
    void touchLRU(int set_idx, int way);

    int m_num_entries;
    int m_num_ways;
    int m_num_sets;
    std::vector<std::vector<Entry>> m_entries;
    std::vector<std::list<int>> m_lru;
};

} // namespace ruby
} // namespace gem5

#endif
