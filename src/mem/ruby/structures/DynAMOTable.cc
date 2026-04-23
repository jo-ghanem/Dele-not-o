/*
 * DynAMOTable implementation. See DynAMOTable.hh for API docs.
 *
 * Paper reference: Soria-Pardos et al., "DynAMO: Improving Parallelism
 * Through Dynamic Placement of Atomic Memory Operations," ISCA 2023 §5.
 */

#include "mem/ruby/structures/DynAMOTable.hh"

#include <cassert>
#include <algorithm>

namespace gem5 {
namespace ruby {

DynAMOTable::DynAMOTable(int num_entries, int num_ways,
                         int threshold, int confidence_max)
    : m_num_entries(num_entries),
      m_num_ways(num_ways > 0 ? num_ways : 1),
      m_num_sets(num_entries > 0 && num_ways > 0
                 ? num_entries / num_ways : 0),
      m_threshold(threshold),
      m_confidence_max(confidence_max)
{
    assert(m_num_sets * m_num_ways == m_num_entries);
    m_entries.assign(m_num_sets, std::vector<Entry>(m_num_ways));
    m_lru.assign(m_num_sets, std::list<int>());
    for (int s = 0; s < m_num_sets; ++s) {
        for (int w = 0; w < m_num_ways; ++w) {
            m_lru[s].push_back(w);
        }
    }
}

int
DynAMOTable::setIndex(Addr addr) const
{
    if (m_num_sets == 0) return 0;
    // Shift off the byte offset (assume 64-byte line) before hashing.
    // Block-address low bits give good set-index distribution.
    Addr blk = addr >> 6;
    return static_cast<int>(blk % m_num_sets);
}

int
DynAMOTable::findWay(int set_idx, Addr addr) const
{
    if (set_idx < 0 || set_idx >= m_num_sets) return -1;
    const Addr blk = addr >> 6;
    for (int w = 0; w < m_num_ways; ++w) {
        const Entry &e = m_entries[set_idx][w];
        if (e.valid && (e.tag >> 6) == blk) return w;
    }
    return -1;
}

int
DynAMOTable::pickVictimWay(int set_idx)
{
    // LRU is the back of the list. If we find an invalid way, prefer it.
    for (int w = 0; w < m_num_ways; ++w) {
        if (!m_entries[set_idx][w].valid) return w;
    }
    return m_lru[set_idx].back();
}

void
DynAMOTable::touchLRU(int set_idx, int way)
{
    auto &lru = m_lru[set_idx];
    lru.remove(way);
    lru.push_front(way);
}

bool
DynAMOTable::has(Addr addr) const
{
    const int s = setIndex(addr);
    return findWay(s, addr) >= 0;
}

void
DynAMOTable::allocate(Addr addr)
{
    const int s = setIndex(addr);
    int w = findWay(s, addr);
    if (w < 0) {
        w = pickVictimWay(s);
    }
    Entry &e = m_entries[s][w];
    e.tag = addr;
    e.valid = true;
    e.reuse_bit = false;
    // Paper §5.3: "setting the confidence counter to its maximum value.
    // Therefore, the next decision for that memory address will be to
    // execute near."
    e.confidence = m_confidence_max;
    touchLRU(s, w);
    ++m_g_brought_in;
}

void
DynAMOTable::mark_reused(Addr addr)
{
    const int s = setIndex(addr);
    const int w = findWay(s, addr);
    if (w < 0) return;  // not tracked — nothing to mark
    m_entries[s][w].reuse_bit = true;
    touchLRU(s, w);
}

void
DynAMOTable::update_confidence(Addr addr)
{
    const int s = setIndex(addr);
    const int w = findWay(s, addr);
    if (w < 0) return;  // not tracked
    Entry &e = m_entries[s][w];
    if (e.reuse_bit) {
        if (e.confidence < m_confidence_max) ++e.confidence;
        ++m_g_reused;
    } else {
        if (e.confidence > 0) --e.confidence;
    }
    e.reuse_bit = false;
    touchLRU(s, w);
}

void
DynAMOTable::evict(Addr addr)
{
    const int s = setIndex(addr);
    const int w = findWay(s, addr);
    if (w < 0) return;
    m_entries[s][w].valid = false;
    m_entries[s][w].reuse_bit = false;
    m_entries[s][w].confidence = 0;
    // Move evicted way to LRU tail so next allocate picks it first.
    m_lru[s].remove(w);
    m_lru[s].push_back(w);
}

bool
DynAMOTable::predict_near(Addr addr) const
{
    const int s = setIndex(addr);
    const int w = findWay(s, addr);
    if (w < 0) return first_decision_near();   // miss → global first-touch
    return m_entries[s][w].confidence > m_threshold;
}

bool
DynAMOTable::first_decision_near() const
{
    // Paper §5.3: "By counting the total number of cache blocks brought into
    // the L1D cache by AMOs, and the total number of these blocks that have
    // been reused, a global view of the amount of local reuse can be
    // obtained. This ratio determines the first decision: if reuse is low,
    // the newly allocated AMO in the AMT will execute far, and near otherwise."
    //
    // First-ever AMO: both counters 0 → default to near (most AMOs benefit
    // from near execution on warm lines; paper also defaults this way).
    if (m_g_brought_in == 0) return true;
    // Ratio check: reuse_rate = g_reused / g_brought_in, threshold is the
    // normalized m_threshold / m_confidence_max.
    // Integer compare to avoid float: g_reused * m_confidence_max >=
    //                                  g_brought_in * m_threshold
    return m_g_reused * static_cast<uint64_t>(m_confidence_max)
           >= m_g_brought_in * static_cast<uint64_t>(m_threshold);
}

void
DynAMOTable::note_amo_fetch()
{
    ++m_g_brought_in;
}

int
DynAMOTable::size() const
{
    int count = 0;
    for (const auto &set : m_entries) {
        for (const auto &e : set) {
            if (e.valid) ++count;
        }
    }
    return count;
}

} // namespace ruby
} // namespace gem5
