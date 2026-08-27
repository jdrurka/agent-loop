# Executor Standards

> Loaded at dispatch time from this file, not copied into the prompt: an edit here reaches every future run without touching the driver's assembly logic. This file owns what good work looks like, from first read to last commit. It does not restate the chain steps, the territory block, the Optimal Path intensity block, or the completion/commit-prefix requirements — the driver assembles those fresh per ticket, and a second copy here would drift from that one.

---

## Before touching anything

- Read the ticket, its named plan sections, and the current implementation before writing a line of code. A plan doc's guess about the codebase is not the codebase.
- Write the Implementation plan into the ticket first, grounded in what the code actually looks like right now.
- Trace the reported vector to the file or function that causes it. If the first thing you find is a symptom, keep tracing until you hit the actual cause.

## What counts as an escalation

Stop the chain and file a P0 discovery, in or out of your declared territory, only for the classes that meet this bar:

- **Data corruption** — a write path that can leave persisted state inconsistent, partially applied, or silently wrong (not just a bug that produces a wrong value in memory).
- **A security hole** — an auth check that can be bypassed, a secret that leaks into a log or a committed file, an injection point reachable from untrusted input.
- **A race that loses data** — concurrent access to shared state with no lock, no atomic operation, or a lock scope that lets a second writer overwrite a first before it lands.

A bug that's merely wrong, slow, or ugly is a normal finding, not a P0. Document the P0, commit what you have, and report it. Do not attempt to fix it yourself unless fixing it is what the ticket already asked for.

## The coding rule

Write the shortest correct code that solves the problem while keeping the codebase's existing architecture and patterns intact. That's the whole rule; everything below is elaboration.

### Match the codebase exactly

Before writing anything, read a handful of files in the same directory or module. Then match:

- Naming — whatever casing and vocabulary they already use.
- Error handling — their wrapping pattern, their error types, their return style.
- Import style — their grouping, their alias conventions, their path resolution.
- Function signatures — parameter order, return types, nil/null handling.
- File organization — section ordering, comment style, export patterns.

If a reviewer can't tell your code from the surrounding code, you wrote it right.

### No over-engineering

| Temptation | Do this instead |
|---|---|
| Abstract base class for one implementation | Write the concrete implementation |
| Factory for one object type | Construct the object directly |
| Event bus for two components talking | A direct function call |
| Config file for one value | Hardcode it; extract later if a second consumer shows up |
| Generic type parameter for one type | Use the concrete type |
| Interface with one implementation | Skip the interface |
| Utility function used once | Inline the logic |
| Custom error type for an internal function | Return a wrapped error with context |
| Middleware for one endpoint | Put the logic in the handler |
| Retry/backoff for an internal call | Let it fail; the caller handles it |

The test: are you adding abstraction for a second consumer that exists today? If not, don't.

### No under-engineering

Don't cut these for brevity:

- Every error path handled. No swallowed errors.
- Input validated at every boundary — user input, API input, responses from anything external.
- The architectural pattern the codebase already uses, every time, not just when convenient.
- Resource cleanup — connections closed, contexts cancelled, locks released.
- Concurrency safety for anything that actually runs concurrently.

### Three lines beats one abstraction

Duplication is cheaper than the wrong abstraction. Three near-identical lines that are each immediately readable beat one clever helper that hides what's happening. Extract only when the pattern repeats four or more times, the logic is genuinely complex enough that a name adds clarity, or the codebase already has a utility built for exactly this.

### No gold-plating

Implement exactly what the ticket asks for. Fixing a null check does not license refactoring the surrounding function. Adding an endpoint does not license adding caching, rate limiting, and observability nobody asked for. Writing tests means testing the specified behavior, not adding benchmarks and fuzz tests on top. Dead code spotted along the way stays untouched unless it clears the P0 bar above — then it's a discovery, not a fix.

### Comments only when non-obvious

Comment the why, never the what. A comment explaining an unusual workaround, a business rule that isn't visible in the code, or a decision that looks wrong until you know the history earns its place. A comment restating what the next line obviously does does not.

---

## Territory and conflicts

Several executors, and the driver itself, may be touching this codebase around the same time.

- Reading outside your declared territory is fine and often necessary for context. Writing outside it is never fine, no matter how small the change looks.
- If your work depends on something another ticket is expected to add and it isn't there yet, code against the interface the plan describes, note the dependency, and never stub or fake the other ticket's implementation in its own territory.
- A change you can see is needed but can't make from inside your territory belongs in Findings under new work discovered, not in your diff.

## Verifying your own work

Run the ticket's exact verify command through the guarded seam before you report anything. The guard exists precisely so verification never touches a live port, a live database, or a real credential; if the only seam available is live, building a throwaway one is the actual first step of the ticket, not a shortcut around it. A verify that exits clean without asserting anything checked has verified nothing — make sure the command's output actually demonstrates the fix, not just a zero exit code.
