# Coinbase lifecycle patch

This patch is independent of vendor SDKs and hardware transport. The owner's original symptom
was a visible reward that could not be constructed/signed for transfer; no failing real
transaction or error was supplied, so that historical root cause remains unproven.

## State and eligibility

Raw transaction parsing identifies a coinbase by its null outpoint. Wallet outputs retain
`IS_COINBASE`, independently of output index: a miner's reward at output 2 is still coinbase;
block position 0 is a separate fact. Existing indexing code already set this output flag; the
new regression protects it. No database migration or retrospective user-database rewrite is
performed here.

A reward at height H becomes eligible for the next block when `tip + 1 >= H + 100`, provided
its current block is verified. Balance categorization and coin selection use the same boundary.
A reported positive height alone is not a merkle proof. Unknown/zero/negative heights cannot
become spendable simply because the local tip is high. Construction and signing recheck known
reward ancestry, including when a previously selected coin survives a tip change.

On chain reorganization, affected settled and cleared entries lose position, verification
state and proof bytes in the database as well as in memory. A reward removed from every
account's subscribed key history is similarly invalidated, including after an offline reorg.
Removal from just one key does not invalidate a reward still reported for another key/account.
A later height notification does not reuse the old proof. Late proof replies must match both
the current block header and the current requested transaction height.

Transaction and output records are retained for inspection/reconfirmation. Known unsettled
descendants of an invalidated or immature reward cannot supply spendable outputs. Invalidated
reward/descendant deltas do not inflate the history running balance. History labels a reward
without a verified block explicitly; it is not shown as an ordinary mempool payment. Absence
is not labelled proof of permanent orphaning: the reward can be proved on the current chain
again, then must meet that height's maturity boundary.

## Evidence and limits

`test_coinbase.py` exercises output-index recognition, maturity at 99/100 confirmations, tip
rollback, real temporary SQLite proof invalidation/reload, changed height, reconfirmation,
retained descendants, selected-coin rejection, subscription removal, and late proof replies
from superseded blocks. It also checks Trezor's null-outpoint encoding. Native proof export
uses the invalidated wallet proof cache. Existing standalone BREAD/BEEF JSON caches and third
party exported proof packages are not retroactively rewritten; their current-chain validity
must be checked when consumed.

This does not replace full-node consensus validation or prove unknown external inputs are
unspent. Server-side indexing, arbitrary mining payout scripts, conflict resolution for
unrelated transactions, and the original reported failing transaction remain outside the
specific regression evidence. No owner wallet has been reindexed and no reward has been spent.
