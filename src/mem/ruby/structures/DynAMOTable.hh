/*
 * DynAMOTable — per-L1 AMT (Atomic Metadata Table) for the DynAMO-Reuse
 * predictor (dynamo.pdf ISCA'23 §5). Set-associative lookup keyed by line
 * address; per-entry reuse_bit + saturating confidence counter; per-L1
 * global counters (g_brought_in / g_reused) used only for the first-touch
 * decision (paper §5.3 "uninformed decision" on AMT miss).
 */

#ifndef __MEM_RUBY_STRUCTURES_DYNAMOTABLE_HH__
#define __MEM_RUBY_STRUCTURES_DYNAMOTABLE_HH__

#include <cstdint>
#include <list>
#include <vector>

#include "mem/ruby/common/Address.hh"

namespace gem5 {
namespace ruby {

class DynAMOTable
{
  public:
    DynAMOTable(int num_entries, int num_ways,
                int threshold, int confidence_max);

    // Is this address currently tracked in the AMT?
    bool has(Addr addr) const;

    // Allocate an entry for this address. Paper §5.3: "setting the confidence
    // counter to its maximum value" so the next decision is near. Also
    // increments g_brought_in (total blocks fetched into L1 via AMO).
    void allocate(Addr addr);

    // Mark the line as reused (set reuse_bit). Called on any non-AMO L1 hit
    // on a tracked line per dynamo.pdf §5.3 ("if that same cache block
    // receives a subsequent hit by any other memory access").
    void mark_reused(Addr addr);

    // Update confidence on L1D eviction or invalidating snoop. If reuse_bit
    // was set: confidence++, g_reused++. Else: confidence--. Clears reuse_bit.
    void update_confidence(Addr addr);

    // Evict the entry (stop tracking). Called when the L1 line is finally
    // removed and we also want to free the AMT slot.
    void evict(Addr addr);

    // Per-entry decision after allocation: predict near iff confidence > 0.
    // (Threshold = 0 means "any residual confidence"; other threshold values
    // available via the ctor / dynamo_threshold Python knob.)
    bool predict_near(Addr addr) const;

    // Global first-touch decision (paper §5.3): AMT miss uses the global
    // reuse ratio. True iff g_reused / (g_brought_in + 1) >= threshold/
    // confidence_max. On the very first AMO (denominators both 0), return
    // true (near) per paper convention.
    bool first_decision_near() const;

    // Record that an AMO brought a block into L1D. Called at allocate time
    // from SLICC; kept as a separate entry point for future flexibility.
    void note_amo_fetch();

    // Diagnostics
    int size() const;
    int sets() const { return m_num_sets; }
    int ways() const { return m_num_ways; }
    uint64_t global_brought_in() const { return m_g_brought_in; }
    uint64_t global_reused() const { return m_g_reused; }

  private:
    struct Entry
    {
        Addr tag = 0;        // line address (block-aligned)
        bool valid = false;
        bool reuse_bit = false;
        int  confidence = 0;
    };

    int setIndex(Addr addr) const;
    int findWay(int set_idx, Addr addr) const;  // -1 if miss
    int pickVictimWay(int set_idx);              // LRU via access list
    void touchLRU(int set_idx, int way);         // move way to MRU

    int m_num_entries;
    int m_num_ways;
    int m_num_sets;
    int m_threshold;        // predict near iff confidence > threshold
    int m_confidence_max;   // saturation cap

    std::vector<std::vector<Entry>> m_entries;          // [set][way]
    std::vector<std::list<int>> m_lru;                   // [set] = list<way>, MRU at front

    uint64_t m_g_brought_in = 0;
    uint64_t m_g_reused = 0;
};

} // namespace ruby
} // namespace gem5

#endif // __MEM_RUBY_STRUCTURES_DYNAMOTABLE_HH__
