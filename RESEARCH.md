# PAD orb recognition notes

Findings gathered with Firecrawl on 2026-08-27:

- The [Puzzle & Dragons Ver. 22.7 update](https://www.puzzleanddragons.us/single-post/ver-22-7-update-2510) uses the official `Enhanced Fire ~ Dark Orbs+` terminology. The `+` is an adjustment to the enhanced-orb skill name, not a new matching attribute.
- The secondary [PAD mechanics board-effects guide](https://gl1tch3d.com/pad-mechanics-board-hazards-and-other-effects/) says the visual enhanced marker is a small `+` at the orb’s bottom-right. It limits enhancement to fire, water, wood, light, dark, and heart; jammer, poison, and bomb orbs cannot be enhanced.
- That guide describes the three requested hazards separately and supplies visual references: [jammer](https://i0.wp.com/gl1tch3d.com/wp-content/uploads/2017/12/8.png?resize=106%2C105&ssl=1), [poison](https://i0.wp.com/gl1tch3d.com/wp-content/uploads/2017/11/orbpoison.png?resize=104%2C104&ssl=1), and [bomb](https://i0.wp.com/gl1tch3d.com/wp-content/uploads/2017/11/orbbomb.png?resize=104%2C104&ssl=1). These are visual references, not an official game specification.

Implementation consequences: enhancement is a flag on the underlying normal colour, so `fire` and `fire+` share one solver match key. Hazards retain their own match keys and are never coerced to a normal colour. Non-hazard screenshot cells are clustered from the current board’s palette; only close matches to the calibrated six hues receive names, and other clusters are reported as `unknown-N`.
