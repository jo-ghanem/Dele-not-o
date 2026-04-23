/*
 * DelegatoPredictor — per-HN predictor for the Delegato dynamic AMO
 * routing policy (chiplet.pdf §5.3). Variant-dispatched so the FSM can
 * be ablated against trivial static baselines without protocol churn.
 *
 * Decision returned per AMO: CENTRALIZE / DELEGATE / MIGRATE.
 *
 * FSM variant (paper §5.3, plan §4.3 delegato_fsm.md):
 *   - CA (Chiplet-Aware) default on allocation
 *   - * → PC on SnpResp[reuse_bit == 0]
 *   - * → PO on Last_Req_ID == Cur_Req_ID (consecutive from same core)
 *   - PO → PO on SnpResp[reuse_bit == 1] (reuse keeps PO)
 *   - Everything else: stay-in-state (implementation choice; paper silent)
 *
 * CA decision mapping (chiplet.pdf §4.3 Table 2, single-chiplet collapse
 * forced by predictors-branch topology — all requesters local):
 *   - line cached at directory or upstream (UC/RSC/UD/RSD) → Centralize
 *   - RU with exclusive owner local → Delegate
 *   - I (no cache holds the line)   → Migrate
 *   - otherwise → Centralize
 */

#ifndef __MEM_RUBY_STRUCTURES_DELEGATOPREDICTOR_HH__
#define __MEM_RUBY_STRUCTURES_DELEGATOPREDICTOR_HH__

#include <cstdint>
#include <list>
#include <vector>

#include "mem/ruby/common/Address.hh"
#include "mem/ruby/common/MachineID.hh"

namespace gem5 {
namespace ruby {

class DelegatoPredictor
{
  public:
    // Decision codes returned to SLICC. Keep integer-valued since SLICC
    // external return types are simpler as plain ints.
    enum Decision
    {
        DECISION_CENTRALIZE = 0,
        DECISION_DELEGATE   = 1,
        DECISION_MIGRATE    = 2,
    };

    enum Variant
    {
        VARIANT_FSM               = 0,   // PO/PC/CA FSM (default)
        VARIANT_ALWAYS_DELEGATE   = 1,   // collapses to hn_amo_policy=1
        VARIANT_ALWAYS_MIGRATE    = 2,   // collapses to hn_amo_policy=3
        VARIANT_ALWAYS_CENTRALIZE = 3,   // collapses to hn_amo_policy=0
    };

    DelegatoPredictor(int num_entries, int num_ways, int variant);

    // Decide routing for an AMO arriving at the HN. `ca_hint_migrate` is
    // a single-chiplet shortcut: when PT returns CA, the caller passes
    // the precomputed "state-I AND no cache holds it" predicate as true
    // to let Table 2's I-row Migrate fire. When false, CA falls through
    // to Delegate-when-exclusive-owner else Centralize.
    int decide(Addr addr, MachineID requestor, bool ca_hint_migrate);

    // Feed back reuse signal from SnpResp and update PT FSM.
    void observe(Addr addr, bool reuse_bit, MachineID requestor);

    // Diagnostics
    int size() const;

  private:
    struct Entry
    {
        Addr tag = 0;
        bool valid = false;
        int  state = 0;          // 0=CA, 1=PC, 2=PO
        MachineID last_req;
        bool last_req_valid = false;
    };

    int setIndex(Addr addr) const;
    int findWay(int set_idx, Addr addr) const;
    int pickVictimWay(int set_idx);
    void touchLRU(int set_idx, int way);
    Entry* findOrAllocate(Addr addr);   // allocate if miss, set state=CA

    int m_num_entries;
    int m_num_ways;
    int m_num_sets;
    int m_variant;

    std::vector<std::vector<Entry>> m_entries;
    std::vector<std::list<int>> m_lru;
};

} // namespace ruby
} // namespace gem5

#endif
