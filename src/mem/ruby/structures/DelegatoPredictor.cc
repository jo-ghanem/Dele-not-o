#include "mem/ruby/structures/DelegatoPredictor.hh"

#include <cassert>

namespace gem5 {
namespace ruby {

DelegatoPredictor::DelegatoPredictor(int num_entries, int num_ways,
                                     int variant)
    : m_num_entries(num_entries),
      m_num_ways(num_ways > 0 ? num_ways : 1),
      m_num_sets(num_entries > 0 && num_ways > 0
                 ? num_entries / num_ways : 0),
      m_variant(variant)
{
    assert(m_num_sets * m_num_ways == m_num_entries);
    m_entries.assign(m_num_sets, std::vector<Entry>(m_num_ways));
    m_lru.assign(m_num_sets, std::list<int>());
    for (int s = 0; s < m_num_sets; ++s)
        for (int w = 0; w < m_num_ways; ++w)
            m_lru[s].push_back(w);
}

int DelegatoPredictor::setIndex(Addr addr) const
{
    if (m_num_sets == 0) return 0;
    return static_cast<int>((addr >> 6) % m_num_sets);
}

int DelegatoPredictor::findWay(int s, Addr addr) const
{
    if (s < 0 || s >= m_num_sets) return -1;
    const Addr blk = addr >> 6;
    for (int w = 0; w < m_num_ways; ++w) {
        const Entry &e = m_entries[s][w];
        if (e.valid && (e.tag >> 6) == blk) return w;
    }
    return -1;
}

int DelegatoPredictor::pickVictimWay(int s)
{
    for (int w = 0; w < m_num_ways; ++w)
        if (!m_entries[s][w].valid) return w;
    return m_lru[s].back();
}

void DelegatoPredictor::touchLRU(int s, int w)
{
    m_lru[s].remove(w);
    m_lru[s].push_front(w);
}

DelegatoPredictor::Entry *
DelegatoPredictor::findOrAllocate(Addr addr)
{
    const int s = setIndex(addr);
    int w = findWay(s, addr);
    if (w < 0) {
        w = pickVictimWay(s);
        Entry &e = m_entries[s][w];
        e.tag = addr;
        e.valid = true;
        e.state = 0;   // CA on allocation (paper §5.3)
        e.last_req_valid = false;
    }
    touchLRU(s, w);
    return &m_entries[s][w];
}

int
DelegatoPredictor::decide(Addr addr, MachineID requestor,
                          bool ca_hint_migrate)
{
    // Static variants collapse the predictor to fixed policies; useful
    // for ablation (plan §6.0 variant set).
    switch (m_variant) {
      case VARIANT_ALWAYS_CENTRALIZE: return DECISION_CENTRALIZE;
      case VARIANT_ALWAYS_DELEGATE:   return DECISION_DELEGATE;
      case VARIANT_ALWAYS_MIGRATE:    return DECISION_MIGRATE;
      case VARIANT_FSM:               break;   // fall through
      default:                        return DECISION_CENTRALIZE;
    }

    Entry *e = findOrAllocate(addr);

    // Paper §5.3 prose: "detects consecutive accesses from the same
    // requester and migrates the cache line to the requester." Apply
    // E3 at decision time so promotion reflects current request before
    // we commit to a route.
    if (e->last_req_valid && e->last_req == requestor) {
        e->state = 2;  // PO
    }
    e->last_req = requestor;
    e->last_req_valid = true;

    int decision;
    switch (e->state) {
      case 2:   // PO
        decision = DECISION_MIGRATE;
        break;
      case 1:   // PC
        decision = DECISION_CENTRALIZE;
        break;
      case 0:   // CA — consult Table 2 via caller's hint
      default:
        decision = ca_hint_migrate ? DECISION_MIGRATE
                                   : DECISION_DELEGATE;
        // Delegate-vs-Centralize resolution happens in SLICC based on
        // dir_ownerExists && dir_ownerIsExcl. Caller handles the guard.
        break;
    }
    return decision;
}

void
DelegatoPredictor::observe(Addr addr, bool reuse_bit, MachineID requestor)
{
    // Static variants don't update FSM state — their decisions ignore it.
    if (m_variant != VARIANT_FSM) return;

    const int s = setIndex(addr);
    const int w = findWay(s, addr);
    if (w < 0) return;   // no entry yet (e.g., observe before first decide)
    Entry &e = m_entries[s][w];

    // Paper §5.3 Fig. 6b: * → PC on reuse_bit == 0.
    // PO → PO on reuse_bit == 1 (implicit stay; already in PO).
    if (!reuse_bit) {
        e.state = 1;  // PC
    }
    // else: stay in current state (no explicit demotion on reuse=1)
    touchLRU(s, w);
}

int DelegatoPredictor::size() const
{
    int c = 0;
    for (const auto &set : m_entries)
        for (const auto &e : set)
            if (e.valid) ++c;
    return c;
}

} // namespace ruby
} // namespace gem5
