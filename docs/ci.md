# CI tiers

| Tier | Runs on | What it runs | Cost |
|---|---|---|---|
| 1 | Every push and pull request (`ci.yml`) | Lint; unit, policy, contract (offline), and graph tests; task validation and gold replay for all three task sets; browser tests on recorded replies; the UI audit, which screenshots every page and state at seven screen sizes (320-pixel phones to 1366 by 768 and 1440 by 900 laptops) after the fonts load and fails on sideways scroll, clipped text, overlaps, a table wider than its box, or a tour card cut off on a laptop (screenshots are kept as an artifact when it fails); a secret scan | Free |
| 2 | Pushes to `main` that change the agent, retrieval, store tools, policies, the simulated store, or the 53-case suite, and manual runs (`agent-checks.yml`) | Starts the agent on the simulated store, runs the scenario check for every homepage button, then the 53-case suite | About $0.09 a run, capped at $0.05 for the scenarios and $0.20 for the suite |
| 3 | Manual only (`sim.yml`) | A plan job prints the task count and cost estimate, then the run job, in the `paid-runs` environment, runs the simulation with a spend cap | Set per run; above $1 needs the approval box |

## Secrets

The paid tiers read only these repository secrets, kept apart from any key used elsewhere:

| Secret | Used by | What it is |
|---|---|---|
| `CI_ANTHROPIC_API_KEY` | Tiers 2 and 3 | An Anthropic API key for CI, ideally in its own workspace with a monthly spend limit |
| `CI_SIM_USER_API_KEY` | Tier 3 | The simulated customer's API key |
| `CI_SIM_USER_BASE_URL` | Tier 3 | The simulated customer's API base URL |
| `CI_SIM_USER_MODEL` | Tier 3 | The simulated customer's model name |

Without `CI_ANTHROPIC_API_KEY`, tier 2 skips its paid steps with a notice instead of failing. To see the
plan before a tier 3 run spends anything, add required reviewers to the `paid-runs` environment in the
repository settings: the run job then waits for approval after the plan job shows the estimate.
