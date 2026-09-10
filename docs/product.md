# V1 Product Contract

This is the short product source of truth for the first playable. Use it to decide whether gameplay work belongs in v1 and to review whether that work serves the intended experience. A material contradiction or change to this contract is a product decision that requires human approval; do not infer a replacement from code, genre convention, or old discussion.

## Player promise and fiction

The game is an original occult/cosmic-horror setting with a small vocabulary that can support later games. The player tries to save the world from an unspeakable horror that cannot be defeated, only delayed. Each run is one life. Lives fail or end, but their legacy changes later attempts; a successful confrontation buys the world another reprieve.

Three power domains establish reusable lore and distinct ways to grow:

- **Beast:** self-transformation and self-empowerment, including vampires, werewolves, hunters, and related material.
- **Necrotic:** minions and created forces, including undead, forbidden magic, and horrific science.
- **Demonic:** bargains and rule-breaking power at a consequence, including contracts and occult, eldritch, or demonic forces.

Identities emerge from the player's choices instead of being selected up front. They should use setting-specific language, not generic fighter/cleric classes. Previous endings may unlock starting options, while some identities remain discoverable only through play. Alignment with, or transformation by, the horror is a valid outcome rather than merely failure; its visibility and rewards are not decided yet.

## Core loop: tasks, time, and attention

The player chooses finite tasks or undertakings while continuous time advances active work, Life, Doom, and manifested event pressures. Tasks may be interrupted or switched when a problem or opportunity appears. Ignoring something can have consequences, so choosing what *not* to attend to is part of play.

Active play should feel like active idle/plate-spinning: several approaching timers or opportunities create mild, legible pressure and ask the player where to put attention. It should not reduce to choosing one task and returning much later.

Walking away must always be acceptable. No task requires the app to remain open. On return, the game resolves only the consequences allowed by the current build and its simulated-time budget, then makes what happened and why understandable.

Automation is a build axis, not a universal convenience endpoint. Choices may extend unattended time, queue work, delegate to minions, absorb risk personally, or transfer risk to expendable agents. These choices should support different engagement postures and identities rather than converge on one optimal automation path.

## Offline progression

Offline progression is part of v1. The baseline is roughly **30 minutes of simulated game time while away**, subject to playtest tuning. Life and Doom advance during that time; offline work is not free time.

Builds may extend or otherwise alter the offline budget through automation and accepted risk. A longer autonomous window can therefore expose the player to consequences bounded out by the default window: for example, a one-hour condition cannot resolve under a 30-minute cap but can resolve after the player builds for more than an hour of autonomy.

Notifications may surface meaningful new decisions or dangers, but are a courtesy and engagement feature, not protection against ordinary punishment. Irreversible unattended outcomes are appropriate only when the player knowingly chose longer or riskier autonomy, or entered a state whose consequences they had reason to understand.

## Discovery and events

Content becomes **eligible** when its preconditions are met; it is not a sequence of tutorial-only incidents. An eligible discovery may surface directly or through exploratory work such as wandering, investigation, or contacts. An event may create a task, problem, opportunity, timer, or choice.

Hidden eligibility normally starts no punitive clock. Consequence clocks begin when an event manifests or becomes known unless a deliberately introduced mechanic says otherwise. Critical progression may be deterministic, while exploratory discovery may use a weighted eligible pool.

Knowledge can be permanent progression. Once a life discovers a danger, cult, location, or similar fact, later lives may identify, seek, avoid, or assess it earlier. The player need not know every hazard in advance: dying in dangerous catacombs is fair when the loss teaches durable information or progression rather than only wasting time. This principle does not require a literal threat-rating system.

For the first slice, one prerequisite-driven discovery and one exploration-mediated discovery are enough to prove the event model before it is generalized.

## Life, Doom, and legacy

- A run is one life. **Life** is a depletable, manipulable personal resource that may eventually be extended or manufactured supernaturally at a cost.
- **Doom** is separate world-level pressure. It persists when a character dies.
- The player may initiate a confrontation strategically, while bad circumstances may force one. A successful confrontation delays the horror and substantially resets Doom; the exact amount is open.
- Whether the whole Doom cycle can be permanently lost remains open until playtesting shows what is fun.
- Failed lives still award meaningful legacy/prestige progress. Run quality and actions may affect permanent bonuses, one-run starting advantages or options, knowledge, and other unlocks.
- Prior lives may leave discoverable residue, but the first slice does not require that mechanic.

Candidate basic stats are **Health, Intellect, and Passion**. They are not a locked or complete model. Madness is likely to be a pressure or consequence, but its representation is open. Growth may be steady, compounding, temporary, permanent, or otherwise qualitatively different; v1 has no required mathematical taxonomy.

## First playable boundary

The slice exists to test the smallest life-to-life loop, not to prove the whole game.

**Life 1** is intentionally scripted to fail and should be playable within roughly the first five minutes after starting. Its failure is a narrative and mechanical reveal: death is progression, not terminal game-over. Systems should be revealed through play rather than front-loaded. A suitable shape is an older investigator discovering Doom too late, permanently learning about the threat and earning a mild-to-moderate legacy benefit before death. The exact cause of death remains open.

**Life 2** must immediately make Life 1 matter through a materially better start, inherited knowledge, a boost or option, and a visible way to pass or handle the first gate differently. Passing that gate must expose the next problem instead of ending in bigger numbers or making the obstacle vanish. Deeper research leading to mental pressure and then a supernatural encounter or temptation is one candidate causal beat, not locked content.

The slice does **not** require the final Doom confrontation, a complete event catalog, all three domains, a generalized minion or automation system, a full class system, prior-life residue, or world-loss mechanics.

## Target and presentation

Design iPhone portrait-first and touch-first. Web is a practical playable and review surface for the same portrait-oriented product, not a separate desktop experience.

V1 is text-first and visually flat. Copy, typography, information hierarchy, timers, simple symbols and panels, and clear state changes should carry the experience. Text must be readable, touch targets usable, critical state must not rely on color alone, and interruption/resume must be understandable.

Sound, screenshake, haptics, particles, rich animation or transitions, bespoke art, and similar juice are deferred by default. An effect belongs in v1 only when its mechanic requires that effect to communicate state or provide interaction—for example, bleeding that obscures the screen and can be wiped away. Do not require generative images or an art-generation pipeline for v1.

## Human playtest contract

Automated checks can verify behavior, but not whether the loop is fun. Manual play should answer:

1. Within the first few minutes, is it clear that the player chooses work while time keeps moving?
2. Does the scripted first failure feel like a meaningful reveal rather than arbitrary punishment?
3. On starting Life 2, is the benefit of failure/prestige immediately legible and desirable?
4. Does Life 2 reveal a reason to continue—a new problem or gate, not only larger numbers?
5. Do overlapping timers and opportunities create useful mild pressure without confusion or exhaustion?
6. Do choices suggest different identities and attention strategies instead of one optimal automation path?
7. Can the player put the phone down, return, and understand what happened and why?
8. Is the text-first portrait UI clear enough to judge the interaction without art or juice hiding a weak premise?
9. If the slice is weak, is the smallest next experiment evident, or does the premise need revision?

## Explicit v1 non-goals

- Backend services, accounts, cloud saves, multiplayer/live-service systems, or leaderboards.
- Monetization, ads/IAP, analytics, or third-party production SDKs without a separate product decision.
- Cross-app synchronization, cross-sell implementation, or a full shared-universe lore bible.
- Full Beast/Necrotic/Demonic content, formal class trees, broad event catalogs, generalized automation/minion frameworks, or every prestige reward type.
- A generative-art pipeline or polish/juice without gameplay meaning.
- Solving permanent world loss or the full Doom cycle before the smaller life-to-life loop proves fun.

## Deliberately open decisions

Do not invent answers to these merely to make an implementation or document look complete:

- The exact cause and fiction of the scripted first death.
- The final stat model and exact role of madness or sanity.
- The first supernatural encounter/domain and its content names.
- Corruption/alignment visibility during a run.
- Prestige formulas and reward tables.
- Doom reset magnitude and cadence, and eventual world-loss semantics.
- Whether a later effect carries gameplay meaning or is polish; decide when its mechanic is introduced.

When proposing work, state which promise, loop requirement, slice boundary, or playtest question makes it necessary for v1. If none applies, defer it. Surface any material contract change for human approval rather than silently implementing it.
