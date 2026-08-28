# PAD Router

This context plans and verifies Puzzle & Dragons orb-routing on a visible game board.

## Language

**Board**:
The row-and-column arrangement of orbs being analysed for one puzzle turn.
_Avoid_: Game screen, grid

**Standard Board**:
The 6-column by 5-row board supported by the initial desktop application.
_Avoid_: Default grid

**Orb**:
One movable board cell, defined by a base type and optional observable state such as enhanced or locked.
_Avoid_: Bead, jewel

**Base Orb Type**:
The underlying kind of an Orb, independent of any observable state.
_Avoid_: Orb state, appearance

**Orb State**:
An observable qualifier of an Orb, such as enhanced or locked, recorded separately from its Base Orb Type.
_Avoid_: Orb type, colour

**Hazard Orb**:
An orb whose removal may be undesirable or harmful: poison, mortal poison, jammer, or bomb.
_Avoid_: Special orb

**Detected Board**:
The Board inferred from a supplied screenshot before a user accepts or corrects it.
_Avoid_: Verified board

**Recognition Rejection**:
A reason a Board cell cannot be trusted: missing detection, ambiguous visual class, a suspected new visual class, or an unstable frame. A Board with a Recognition Rejection is not a Confirmed Board.
_Avoid_: Uncertain result, bad read

**Unknown Orb**:
An Orb whose visual type cannot be safely assigned to a known type. It may be ambiguous or a suspected new visual class.
_Avoid_: New orb, unrecorded orb

**Recognition Corpus**:
The permanent local collection of cell visual-feature samples and annotations used to calibrate recognition.
_Avoid_: Temporary screenshots, cloud dataset

**Human Annotation**:
A user's permanent classification or Recognition Rejection for one cell in a Board capture.
_Avoid_: Mark, correction

**Implicit Annotation**:
A low-weight recognition observation recorded when a user moves to the next Board capture without correcting the current one. It can help classify a later Unknown Orb, while Human Annotations take priority when evidence conflicts.
_Avoid_: Ground truth, auto-confirmation

**Confirmed Board**:
A Detected Board with no Recognition Rejection. It becomes eligible for route execution checks automatically after recognition or a Human Annotation changes.
_Avoid_: User-approved board, valid board

**Board Calibration**:
The user-approved mapping from a screenshot to the cells of a Standard Board.
_Avoid_: Screen settings

**Match**:
A connected orthogonal group of at least three same-type orbs cleared together.
_Avoid_: Clear, 消珠

**Combo**:
One or more matches resolved in the same turn, including subsequent cascade matches when cascade scoring is enabled.
_Avoid_: Chain

**Leader Condition**:
A predicate over the resolved matches that must hold for a chosen leader skill to activate.
_Avoid_: Leader skill, trigger

**Condition Group**:
One user-configured set of Leader Conditions evaluated with an explicit all-of or any-of operator.
_Avoid_: Rule list

**Rule Profile**:
A named local preset containing Team Conditions, External Conditions, and a Hazard Policy.
_Avoid_: Team, build

**External Condition**:
A non-board prerequisite, such as HP or skill state, confirmed by the user rather than inferred from the Board.
_Avoid_: Leader condition

**Team Condition**:
The conjunction of the selected leader conditions; it is satisfied only when every enabled leader condition is satisfied.
_Avoid_: Leader condition

**Route**:
The ordered sequence of board cells traversed while dragging one orb.
_Avoid_: Solution, path

**Candidate Route**:
A route produced for inspection; it is executable only when the board is certain and its Team Condition is satisfied.
_Avoid_: Valid route, solution

**Confirmed Route**:
A Candidate Route explicitly approved after its condition and board checks pass.
_Avoid_: Executed route

**Hazard Policy**:
The chosen treatment of Hazard Orbs during route search; the initial policy avoids matching them unless a Leader Condition requires it.
_Avoid_: Orb safety
