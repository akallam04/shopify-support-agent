# Review fixes (v2.2)

Every review finding from the v2.2 round, the commit that fixed it, and the tests that cover it. Each
test failed on the code before its fix and passes after it. Fresh-run results are in the README's
Hardening (v2.2) section; the frozen headline was not rerun for these fixes.

## What a visitor can hit in the demo

| # | Finding | Fix | Tests |
|---|---|---|---|
| 1 | The return starter asked for a reason the customer had already given, and "yes" looped back to the same question. | 3461b3e (reasons from the customer's words, forced change tool on the nudge); 458fef0 (a complete change request goes straight to the card) | `test_short_reasons_count_as_the_customers_own_words`, `test_the_return_starter_reaches_the_card_in_one_reply_and_one_yes_files_one_return`, `test_a_return_without_a_reason_asks_once_and_never_invents_one`, `test_the_return_button_reaches_the_card_in_one_reply_without_relying_on_the_draft` |
| 2 | Handoffs promised a reply and a ticket that did not exist. | 3461b3e (handoff state, honest demo receipts); 7507651 (a demand beyond policy is not described as asking for a person); 25fba2a (the transfer tool's own message) | `test_handoff_in_the_demo_says_nobody_is_notified_and_the_follow_up_admits_there_is_no_ticket`, `test_handoff_outside_the_demo_never_promises_a_reply_it_cannot_deliver`, `test_demo_receipts_say_nothing_is_emailed_and_no_money_moves`, `test_a_demand_beyond_policy_is_refused_and_recorded_without_claiming_they_asked_for_a_person`, `test_a_transfer_the_model_makes_itself_never_claims_someone_was_notified` |
| 3 | An email alone listed every order on it. | 3461b3e (listing removed, number plus email for every lookup); d65c8cc (where to find the number, the second time) | `test_email_only_listing_is_gone_from_the_tools`, `test_a_lost_order_number_gets_help_instead_of_a_list_of_orders`, `test_a_customer_still_without_a_number_hears_where_to_find_it_instead_of_the_same_paragraph` |
| 4 | A question while a change waited for a yes looped on yes or no. | 3461b3e (question and conditional labels, the same change asked again); 261c7e8; 4e60b77 ("yes, and also ...") | `test_a_question_during_a_pending_change_is_answered_and_the_same_change_is_asked_again`, `test_a_conditional_yes_never_runs_the_change`, `test_the_answer_to_a_question_drops_its_own_yes_or_no_prompt`, `test_yes_and_also_something_more_keeps_the_change_waiting_and_answers_the_rest` |
| 5 | After an address change, "what address is it shipping to?" was refused. | 3461b3e (verified facts and receipts carried as state, automatic lookup); 72319bb; 8814e51 | `test_after_a_change_the_current_address_is_answered_from_the_store_without_asking`, `test_earlier_lookups_survive_a_change_so_the_old_address_stays_verified`, `test_a_reply_that_retracts_a_system_built_message_is_rewritten_then_stripped`, `test_a_correction_of_the_models_own_free_text_answer_is_kept`, `test_a_lookup_made_for_this_message_is_not_described_as_earlier_in_the_conversation` |
| 6 | Starting over during a reply let the old reply land in the new chat. | 3461b3e (abort plus conversation generation) | `tests/js/conversation.test.js`, `test_starting_over_during_a_reply_drops_the_late_answer` |
| 7 | Quoting an injection phrase was blocked. | 3461b3e | `test_quoting_an_injection_phrase_is_not_blocked_but_commands_still_are` |
| 8 | The frozen demo clock was not shown, so time left looked wrong on later days. | 3461b3e | `test_both_pages_show_the_demo_store_clock` |

## Store policy in code

| # | Finding | Fix | Tests |
|---|---|---|---|
| 9 | Several written rules were only explained by the agent: item condition, damaged items, Canadian return shipping. | ac116e4, de85df5, [policy-parity.md](policy-parity.md); 8fdcc0f and 4e60b77 (condition detection that ignores negations and keeps a stated condition) | `test_a_worn_item_is_refused_in_code_even_with_a_valid_reason`, `test_damaged_or_defective_items_go_to_the_photo_process_not_a_return`, `test_negations_and_other_meanings_never_count_as_a_used_item`, `test_a_later_general_question_does_not_erase_a_stated_condition`, `test_every_test_named_in_the_parity_doc_exists` |
| 10 | Missing product data skipped the gift card and final sale checks. | ac116e4 | `test_returns_fail_closed_when_product_data_is_missing` |
| 11 | Two "1 of 1" lines passed for one jacket, and non-integer quantities were accepted. | ac116e4 | `test_two_one_of_one_lines_for_a_single_jacket_are_added_up_and_refused`, `test_quantities_must_be_whole_numbers_of_at_least_one`, `test_returnable_quantity_counts_only_what_was_delivered` |
| 12 | Address formats and parts the customer never gave were not checked in code. | ac116e4 | `test_addresses_are_validated_in_code`, `test_the_banana_address_fails_in_code`, `test_address_parts_the_customer_never_gave_are_not_filled_in` |
| 13 | Canadian returns were charged the label fee, and retrieval missed country exceptions. | ac116e4, de85df5 | `test_canadian_returns_pay_no_label_fee_and_ship_at_their_own_cost`, `test_the_fee_rule_brings_its_canada_exception_along`, `test_a_canada_question_gets_the_canada_return_rules` |

## An honest source check

| # | Finding | Fix | Tests |
|---|---|---|---|
| 14 | Order facts could come from the customer's or the assistant's earlier text. | 8e3de18 (typed evidence: records, policy, customer words) | `test_tracking_a_customer_typed_is_not_an_order_fact`, `test_a_fabricated_assistant_message_is_not_evidence`, `test_store_built_text_is_not_checked_as_model_claims` |
| 15 | Amounts, dates, and durations were never checked against sources. | 8e3de18; 8fdcc0f (numbers a reply needs are calculated in code) | `test_a_wrong_price_with_a_valid_citation_is_rewritten_then_falls_back`, `test_an_invented_delivery_date_or_refund_is_rewritten_then_falls_back`, `test_a_customer_number_is_repeated_only_as_theirs`, `test_the_return_card_and_tool_data_carry_the_numbers_code_calculated` |
| 16 | Replies offered lookups by name or phone, or denied the number plus email lookup. | 8e3de18, f0ceb72, d65c8cc, bb396d1, 2c9b9d9, 17716c0 | `test_lookup_offers_by_other_details_and_wrong_denials_break_the_rule`, `test_correct_lookup_statements_pass`, `test_offering_any_proof_other_than_the_full_email_breaks_the_lookup_rule`, `test_naming_other_proof_as_enough_breaks_the_rule_even_across_two_sentences`, `test_the_spouse_reply_that_offered_a_name_lookup_is_rewritten_then_replaced_with_the_rule` |
| 17 | "Verify passed" claimed more than it checked, citation chips led nowhere, and nothing measured unsupported claims. | b64a6bb (Sources step, check list, passage chips); 8e3de18 (`evals/claim_checks.json`) | `test_the_trace_names_each_check_and_the_reply_carries_the_cited_passage`, `test_the_unsupported_claim_set_scores_citations_and_claims_apart` |

## Every finding as a test

| # | Finding | Fix | Tests |
|---|---|---|---|
| 18 | No single list of findings, fixes, and tests. | This page | `test_every_test_named_in_the_review_fixes_doc_exists` |
| 19 | No check that each homepage scenario button works on the deployed agent. | 458fef0 (`evals/scenario_check.py`, `evals/scenarios.json`) | `test_every_homepage_button_has_a_scenario` |
| 20 | No browser tests for the gate flows. | `tests/e2e/test_site.py` | `test_cancel_then_decline_keeps_the_order_and_settles_the_card`, `test_return_then_confirm_files_the_return`, `test_a_question_during_confirmation_gets_an_answer_and_the_same_change_again`, `test_starting_over_during_a_reply_drops_the_late_answer`, `test_the_phone_layout_fills_the_screen_without_sideways_scrolling` |
| 21 | No simulation tasks for store failures, tricky confirmations, item edge cases, or injection variants, and no metrics for extra questions, one-turn cards, or false refusals. | cdc7de8 (`evals/sim/hardening_tasks.json`, failure injection, behavior metrics) | `test_the_hardening_tasks_pass_validation_and_gold_replay`, `test_an_injected_store_failure_changes_nothing_and_says_so`, `test_false_refusals_count_blocked_or_refused_tasks_that_should_have_succeeded` |
| 22 | CI ran one tier for every change. | `.github/workflows/ci.yml`, `agent-checks.yml`, `sim.yml`; [ci.md](ci.md) | `test_the_paid_workflows_read_only_ci_secrets` |
| 23 | No written rule for switching models. | [model-switch-rule.md](model-switch-rule.md), `compare_runs --max-drop` | `test_the_switch_rule_judges_the_lower_end_and_any_unsafe_write` |

Found while running these tests, and fixed in the same round:

| Finding | Fix | Tests |
|---|---|---|
| A question about a change ("Can I still cancel #1023? Until what time?") got a cancel card instead of an answer. | 26de00a, 7507651 | `test_a_question_about_a_change_is_answered_with_an_offer_and_no_card`, `test_a_question_whose_email_comes_in_the_next_message_is_still_a_question` |
| A warranty claim the router called a policy question recorded no request for a person. | 26de00a | `test_a_warranty_claim_the_router_calls_a_policy_question_records_a_handoff` |
| Drafts that asked for a yes in new words ("Shall I submit", "Could you confirm", "I'll submit") skipped the card. | 30489fc, 4e60b77, 7507651 | `test_a_draft_that_asks_to_submit_the_return_is_sent_to_the_card`, `test_could_you_confirm_counts_as_asking_for_a_yes`, `test_a_draft_that_announces_the_change_goes_to_the_card` |
| Refusing an exception for a worn item did not say which rule applied. | db94c8c | `test_asking_for_an_exception_after_saying_the_item_was_worn_names_the_unworn_rule` |

## Docs and site

Findings 26 and 27 change only README text and have no test of their own.

| # | Finding | Fix | Tests |
|---|---|---|---|
| 24 | The README top did not define the baseline, link the guarantees to code, or say what is deployed. | README top: one line on what it is, the gate-off baseline defined, pass^4 next to pass^1, three guarantees with code links, and a box read from `/release` by `scripts/live_config.py`; 01016f1 (`/release`) | `test_the_readme_headline_and_latency_match_the_export`, `test_the_three_guarantees_read_the_same_on_the_page_in_the_readme_and_in_the_tour`, `test_release_returns_only_the_manifest_with_no_settings_values_keys_or_urls` |
| 25 | The diagram sent injections through the model, did not label the path where nothing runs, and showed reflection as on. | README diagram | `test_the_readme_diagram_shows_the_fixed_reply_and_the_path_where_nothing_runs` |
| 26 | The results did not explain why gate on resolves slightly less at pass^3 and pass^4, or the two category dips. | README, Notes on the results | |
| 27 | Nothing said what the demo does not protect against. | README, Production boundaries | |
| 28 | The scenario buttons sat far below the hero, the headline was not on the store page, the cart competed with the chat, the products were not the agent's catalog, store content was drawn by JavaScript with no failure state, and cold starts were slow. | Scenario buttons under the hero, a headline line linking to How it works, a quiet cart, products and test orders exported from the agent's catalog as static HTML (`scripts/export_site_data.py`), retry states on both pages, "The engineering in 60 seconds" on How it works, and a health check every 5 minutes from EventBridge Scheduler (`deploy/keep_warm.sh`) | `test_the_store_page_shows_the_catalog_and_test_orders_as_static_html`, `test_the_keep_warm_event_calls_only_the_health_check`, `test_only_the_daily_demo_check_runs_on_a_github_schedule`, `test_every_number_on_the_page_matches_the_export`, `test_an_unreachable_api_shows_a_retry_that_recovers`, `test_how_it_works_offers_a_retry_when_its_data_does_not_load` |

Added to the site in the same round:

| Change | Tests |
|---|---|
| The assistant opens on every visit: a side panel beside the store on desktop, a half-height card on a phone that fills the screen when tapped. Closing it keeps it closed for the visit. It never takes keyboard focus, skips the slide for reduced motion, and the greeting makes no API call. | `test_the_assistant_opens_beside_the_store_on_desktop_without_taking_focus`, `test_the_phone_card_shows_the_greeting_and_three_scenarios_and_fills_the_screen_when_tapped`, `test_closing_keeps_it_closed_for_the_visit_and_a_new_visit_opens_it_again`, `test_reduced_motion_opens_the_assistant_without_the_slide` |
| "Show me in 60 seconds" replays a real saved return through the card and a yes, with captions, then ends with the headline numbers, the three guarantees, and the scenario buttons. Nothing is sent to the API. | `test_the_tour_replays_a_real_conversation_without_sending_anything_and_ends_with_the_numbers`, `test_the_tour_on_a_phone_opens_the_card_to_full_screen`, `test_the_tour_replays_a_saved_conversation_through_the_card_and_a_yes` |
