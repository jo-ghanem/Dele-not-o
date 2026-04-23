#include "mem/ruby/structures/DelegatoReuseTable.hh"

#include <cassert>

namespace gem5 {
namespace ruby {

DelegatoReuseTable::DelegatoReuseTable(int num_entries, int num_ways)
    : m_num_entries(num_entries),
      m_num_ways(num_ways > 0 ? num_ways : 1),
      m_num_sets(num_entries > 0 && num_ways > 0
                 ? num_entries / num_ways : 0)
{
    assert(m_num_sets * m_num_ways == m_num_entries);
    m_entries.assign(m_num_sets, std::vector<Entry>(m_num_ways));
    m_lru.assign(m_num_sets, std::list<int>());
    for (int s = 0; s < m_num_sets; ++s)
        for (int w = 0; w < m_num_ways; ++w)
            m_lru[s].push_back(w);
}

int DelegatoReuseTable::setIndex(Addr addr) const
{
    if (m_num_sets == 0) return 0;
    return static_cast<int>((addr >> 6) % m_num_sets);
}

int DelegatoReuseTable::findWay(int s, Addr addr) const
{
    if (s < 0 || s >= m_num_sets) return -1;
    const Addr blk = addr >> 6;
    for (int w = 0; w < m_num_ways; ++w) {
        const Entry &e = m_entries[s][w];
        if (e.valid && (e.tag >> 6) == blk) return w;
    }
    return -1;
}

int DelegatoReuseTable::pickVictimWay(int s)
{
    for (int w = 0; w < m_num_ways; ++w)
        if (!m_entries[s][w].valid) return w;
    return m_lru[s].back();
}

void DelegatoReuseTable::touchLRU(int s, int w)
{
    m_lru[s].remove(w);
    m_lru[s].push_front(w);
}

bool DelegatoReuseTable::has(Addr addr) const
{
    return findWay(setIndex(addr), addr) >= 0;
}

void DelegatoReuseTable::allocate(Addr addr)
{
    const int s = setIndex(addr);
    int w = findWay(s, addr);
    if (w < 0) w = pickVictimWay(s);
    Entry &e = m_entries[s][w];
    e.tag = addr;
    e.valid = true;
    e.reuse = false;     // fresh allocation: no reuse yet
    touchLRU(s, w);
}

void DelegatoReuseTable::mark_reused(Addr addr)
{
    const int s = setIndex(addr);
    const int w = findWay(s, addr);
    if (w < 0) return;   // not tracked — no-op
    m_entries[s][w].reuse = true;
    touchLRU(s, w);
}

bool DelegatoReuseTable::lookup_and_reset(Addr addr)
{
    const int s = setIndex(addr);
    const int w = findWay(s, addr);
    if (w < 0) return false;   // not tracked → reuse_bit = false
    bool bit = m_entries[s][w].reuse;
    m_entries[s][w].reuse = false;
    touchLRU(s, w);
    return bit;
}

void DelegatoReuseTable::evict(Addr addr)
{
    const int s = setIndex(addr);
    const int w = findWay(s, addr);
    if (w < 0) return;
    m_entries[s][w].valid = false;
    m_entries[s][w].reuse = false;
    m_lru[s].remove(w);
    m_lru[s].push_back(w);
}

int DelegatoReuseTable::size() const
{
    int c = 0;
    for (const auto &set : m_entries)
        for (const auto &e : set)
            if (e.valid) ++c;
    return c;
}

} // namespace ruby
} // namespace gem5
