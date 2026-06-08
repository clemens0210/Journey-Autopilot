# Context Record — Journey Autopilot

A locked snapshot of all decisions, constraints, and open questions captured during the clarification phase. Serves as the single source of truth for why the architecture looks the way it does.


---

## Table of Contents

- [Feature Streams](#feature-streams)
- [Tech Stack & Tool Architecture](#tech-stack--tool-architecture)

---

## Feature Streams

### Onboarding & Profile 

**Status:** `planned` | **Priority:** Must | **Responsible Person:** Hendrik

**Description:** Capture a personal preference profile (class, seat, speed-vs-comfort trade-off, home constraints) via quick onboarding; import existing DB / BahnBonus account.

#### Decisions
- 

#### Constraints
- 

#### Open Questions
- How do we integrate our tool into the DB Navigator technically and logically? 
- What is all part of the inboarding? What is mandatory and what is voluntary?
- In which UI happens the onboarding? Integrated into the DB Navigator?


#### Justification
- 

### Disruption Monitoring & Risk Prediction

**Status:** `planned` | **Priority:** Must | **Responsible Person:** Clemens & Hendrik

**Description:** Continuously ingest live ops data; score disruption risk **hours in advance** using live data, weather, historical patterns and large events. (Basic systems only react; prediction is the differentiator.)

#### Decisions
- 

#### Constraints
- 

#### Open Questions
- Which DB ops APIs are actually available?

#### Justification
- 

### Replanning / Rerouting

**Status:** `planned` | **Priority:** Must | **Responsible Person:** Clemens

**Description:** On a detected or predicted disruption, generate alternative routes within the DB ecosystem, optimizing arrival time and comfort under the user's constraints.

#### Decisions
- 

#### Constraints
- 

#### Open Questions
- 

#### Justification
- 

### Booking & Execution

**Status:** `planned` | **Priority:** Should | **Responsible Person:** TBD

**Description:** Book or modify tickets, hotels, Flinkster (car-share), Call-a-Bike and taxis as needed; integrate with the consultancy/corporate booking system. Actions must be idempotent and reversible where possible.

#### Decisions
- 

#### Constraints
- Idempotency requirement for external integrations
- Reversibility where possible

#### Open Questions
- Which booking systems to integrate with first?
- How to handle partial failures in multi-booking scenarios?

#### Justification
- 

### Calendar & Constraint Handling

**Status:** `planned` | **Priority:** Must | **Responsible Person:** Martin

**Description:** Read the calendar for meeting times/locations, private appointments; add new routes to the calendar.

#### Decisions
- 

#### Constraints
- 

#### Open Questions
- Which calendar providers to support (Outlook, Google, etc.)?
- Permission model for reading/writing calendar events?

#### Justification
- 

### Stakeholder Communication & Negotiation

**Status:** `planned` | **Priority:** Should | **Responsible Person:** Martin

**Description:** Proactively inform clients, colleagues and private contacts (WhatsApp, Outlook); coordinate across multiple travelers heading to the same meeting.

#### Decisions
- 

#### Constraints
- 

#### Open Questions
- WhatsApp integration approach (official API vs. third-party)?
- Approval workflow for outbound communications?

#### Justification
- 

### User Control & Autonomy

**Status:** `planned` | **Priority:** Must | **Responsible Person:** TBD

**Description:** Every external action (message, booking, claim) passes a veto/approval step with a clear time window; configurable autonomy levels (notify-only → approve-each → auto within limits).

#### Decisions
- 

#### Constraints
- All external actions require approval mechanism
- Configurable autonomy levels

#### Open Questions
- Default approval timeouts per action type?
- How to handle time-critical decisions (e.g., last-minute rebooking)?

#### Justification
- 

### Compensation (Fahrgastrechte)

**Status:** `planned` | **Priority:** Must | **Responsible Person:** TBD

**Description:** Detect delay-compensation eligibility per DB passenger-rights rules (e.g. ≥60 min and ≥120 min delay thresholds), auto-prepare and file claims with the required evidence, and track claim status — no manual filing by the user.

#### Decisions
- 

#### Constraints
- Must follow official DB Fahrgastrechte rules
- ≥60 min and ≥120 min delay thresholds

#### Open Questions
- Which compensation channels to support (DB directly, third-party claim services)?
- How to track claim status over time?

#### Justification
- 

### Memory & Learning Across Trips

**Status:** `planned` | **Priority:** Should | **Responsible Person:** TBD

**Description:** Persist preferences, constraints and outcomes between journeys; prediction quality improves with usage history.

#### Decisions
- 

#### Constraints
- 

#### Open Questions
- What metrics to track for learning?
- How long to retain historical data?

#### Justification
- 

## Tech Stack & Tool Architecture

### Decisions

- **Language & Framework:**
  - Backend: Python
  - Orchestration: Google Generative AI Data Connector (ADK)
  - LLM Integration: LiteLLM with University of Cologne GPT endpoint

- **Data & APIs:**
  - University of Cologne GPT (OpenAI-compatible endpoint) as primary LLM
  - inofficial DB APIs for train data, disruption information, ticket booking
  - Calendar APIs 
  - WhatsApp APIs (Twilio)

### Constraints

- 

### Open Questions

- Real-time data ingestion architecture for live disruption monitoring?
- Storage solution for context/memory (DB, vector store, etc.)?
- Deployment target (cloud platform, on-premise)?
- Authentication/authorization strategy for user data?
- How to handle multi-agent coordination (Monitoring, Planner, Orchestrator)?

### Justification

- Using Google ADK for workflow orchestration and agent coordination
- LiteLLM provides flexibility for model switching
- ReAct agent pattern for decision-making
