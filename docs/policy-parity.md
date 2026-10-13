# Policy parity

Every rule in `data/policies/` and where it is enforced. A rule is either checked in code, with the
test that covers it, or explained by the agent only, with the reason it cannot be checked in code.
"Explained by the agent only" means the agent answers from the policy text through retrieval, with a
citation, and no tool can act against the rule.

Code paths: `mcp_server/policy.py` holds the store rules. `mcp_server/tools.py` resolves items and
builds the confirmation text. The agent side reads the customer's own words: `app/agent/reasons.py`
(return reasons and item condition), `app/agent/addresses.py` (address parts), and the gate in
`app/agent/nodes/gate.py`. Policy tests live in `tests/test_policy.py`, `tests/test_writes.py`, and
`tests/test_policy_parity.py`.

## Returns (`returns.md`)

| Rule | Enforced by | Test |
|---|---|---|
| Returns start within 30 days of delivery. | Code: `policy.can_return` (`RETURN_WINDOW`, delivery date from the fulfillment). The order status gives the last day and the days left, and a refusal names the day the window closed. | `test_return_inside_the_window`, `test_return_at_exactly_thirty_days_is_allowed`, `test_return_after_thirty_days_is_refused`, `test_return_before_delivery_is_refused`, `test_a_return_past_its_window_names_the_day_it_closed` |
| Items must be unworn and unused; worn, washed, or outdoor-used items cannot be accepted. | Code: the gate reads the customer's words clause by clause (`reasons.item_condition`). Only a plain statement that the item was worn, washed, used, or lost its tags refuses, through `policy.can_return` with `item_used`. A negation, a size or chart mention, or a color complaint is not a condition; a hypothetical or general question is unclear and does not refuse. The gate trace keeps the customer's quote. The confirmation card states that the items must be unworn and unused, so the customer's yes covers unclear cases, and the warehouse inspects the return. | `test_a_worn_item_is_refused_in_code_even_with_a_valid_reason`, `test_every_used_condition_is_refused`, `test_the_return_card_says_the_item_must_be_unworn_and_unused`, `test_negations_and_other_meanings_never_count_as_a_used_item`, `test_a_hypothetical_or_general_question_is_unclear_and_not_refused`, `test_an_unclear_condition_shows_the_card_and_records_why` |
| Original tags attached and original packaging included. | Code for tags the customer says are gone (`tags_removed`). Packaging is explained by the agent only: nothing in the order data shows packaging, and the warehouse checks it on arrival. | `test_every_used_condition_is_refused` |
| To start a return, email support; US orders get a prepaid label within 1 business day. | Code: `policy.return_terms` puts the label terms on the card and the receipt. The demo says nothing is emailed. | `test_return_terms_follow_the_policy`, `test_demo_receipts_say_nothing_is_emailed_and_no_money_moves` |
| Canadian customers ship returns at their own cost. | Code: `policy.return_terms` for a Canadian shipping address. Retrieval brings this chunk along with the fee rule, and with any return answer to a question that mentions Canada (`retrieve.companion_ids`). | `test_canadian_returns_pay_no_label_fee_and_ship_at_their_own_cost`, `test_the_fee_rule_brings_its_canada_exception_along`, `test_a_canada_question_gets_the_canada_return_rules` |
| A flat 7.50 USD fee is deducted when the prepaid label is used. | Code: `policy.return_terms`. The fee applies only with the prepaid label, so Canadian returns, which do not use it, have no fee. The policy text now says so in the fee section too. The refund after the fee is calculated in code (`policy.refund_amount`) and shown on the card, the receipt, and the order status, so the agent never does the subtraction. | `test_return_terms_follow_the_policy`, `test_canadian_returns_pay_no_label_fee_and_ship_at_their_own_cost`, `test_refund_amounts_follow_the_fee_rules` |
| The fee is waived if the item arrived damaged, defective, or not as ordered. | Code: `FEE_WAIVED_REASONS` for not as ordered or described. Damaged and defective items go through their own process, below. | `test_return_terms_follow_the_policy` |
| Refunds go to the original payment method in 5 to 7 business days after inspection; original shipping is not refunded unless the store erred. | Explained by the agent only: the agent issues no refunds. The terms appear on the receipt as store policy, and the demo says no money moves. | `test_demo_receipts_say_nothing_is_emailed_and_no_money_moves` |
| Exchanges are free the first time. | Explained by the agent only: there is no exchange tool. | none |
| Gift cards and FINAL SALE items cannot be returned. | Code: `policy.can_return` (`is_gift_card`, `final-sale` tag). Fails closed: a missing product, a missing product id, a deleted product, or unknown tags refuse with `product_unverified` instead of skipping these checks. | `test_final_sale_items_cannot_be_returned`, `test_gift_cards_cannot_be_returned`, `test_returns_fail_closed_when_product_data_is_missing` |
| Opened ski wax and other consumables cannot be returned. | Code when the customer says they opened it (`opened` condition, refused with `opened_consumable`). Whether an item is a consumable comes from the customer's words; the catalog has no consumable flag. | `test_an_opened_consumable_is_refused` |
| Damaged or defective items: email photos within 7 days of delivery for a replacement or full refund. | Code: `policy.can_return` refuses a return with the defective reason (`damaged_or_defective`) and explains the photo process. The agent can record a request for a person (`transfer_to_human`). | `test_damaged_or_defective_items_go_to_the_photo_process_not_a_return` |
| Warranty: 1 year against manufacturing defects, same photo process. | Explained by the agent only: claims need photos and a person's judgment. The agent records a request for a person. | `handoff-warranty` simulation task |
| Only the items and quantities the customer names, and never more than was delivered minus what is already being returned. | Code: `tools._resolve_items` adds up repeated lines per order line and accepts only whole numbers of 1 or more; `policy.can_return` compares the total with the delivered quantity (`delivered_quantity`) minus earlier return requests. | `test_two_one_of_one_lines_for_a_single_jacket_are_added_up_and_refused`, `test_quantities_must_be_whole_numbers_of_at_least_one`, `test_returnable_quantity_counts_only_what_was_delivered`, `test_return_quantity_counts_items_already_requested` |
| A return needs the reason the customer gave. | Code: the gate takes the reason from the customer's own words (`reasons.stated_reason`, `reasons.grounded`) and never uses one they did not give. | `test_short_reasons_count_as_the_customers_own_words`, `test_a_return_without_a_reason_asks_once_and_never_invents_one` |

## Shipping (`shipping.md`)

| Rule | Enforced by | Test |
|---|---|---|
| Processing times and the 12:00 pm cutoff. | Explained by the agent only: no tool changes when an order ships. | none |
| Domestic shipping prices and times. | Explained by the agent only: the agent quotes the policy text; it does not charge shipping. | 53-case policy cases |
| International shipping to Canada only. | Code: `policy.can_change_address` (`SHIP_TO_COUNTRIES`). Retrieval brings the Canada chunk along with the domestic options. | `test_canada_is_a_valid_destination`, `test_address_outside_us_and_canada_is_refused` |
| Import duties are the recipient's responsibility. | Explained by the agent only. | none |
| Tracking numbers. | Code: the status tool returns tracking from the order. | `test_order_status_view` |
| Address changes within 2 hours, before processing. | Code: `policy.order_change`, shared with cancellation. | `test_address_change_inside_the_window`, `test_address_change_follows_the_same_window` |
| A shipping address must be a real, complete address. | Code: `policy.can_change_address` checks the state or province code for the country, the ZIP (5 digits or ZIP+4) or Canadian postal code format, and that no part is empty or only spaces. The gate refuses address parts the customer never gave (`addresses.ungrounded_parts`), so nothing is filled in from the model's general knowledge. | `test_addresses_are_validated_in_code`, `test_the_banana_address_fails_in_code`, `test_address_parts_the_customer_never_gave_are_not_filled_in`, `test_whitespace_only_address_fields_count_as_missing` |
| Delayed or lost packages: a carrier investigation after 7 or 21 days. | Explained by the agent only: investigations need a person and the carrier. | none |

## FAQ (`faq.md`)

| Rule | Enforced by | Test |
|---|---|---|
| Price adjustments within 14 days, once per order, not between sale events or for FINAL SALE. | Explained by the agent only: there is no refund or adjustment tool. | `policy-price-adjustment` simulation task |
| Gift cards are digital, never expire, carry no fees, and cannot be refunded or exchanged for cash. | Code for returns (`policy.can_return`, gift card). The rest is explained by the agent only. | `test_gift_cards_cannot_be_returned` |
| Orders can be changed or cancelled within 2 hours if not yet processing. | Code: `policy.order_change`. "Processing" is treated as 2 hours after placement or any shipment, whichever comes first. | `test_cancel_inside_the_window`, `test_cancel_at_exactly_two_hours_is_allowed`, `test_cancel_after_two_hours_is_refused`, `test_cancel_refused_once_shipped`, `test_shipped_wins_over_the_window` |
| Online only, shipping from Denver. | Explained by the agent only. | none |
| Snowboard sizing. | Explained by the agent only. | 53-case product cases |
| No price matching with other retailers. | Explained by the agent only. | none |
| Support by email, Monday to Friday, 8 am to 5 pm Mountain Time, within 1 business day. | Explained by the agent only. The agent never promises a reply itself: a request for a person is recorded, and the reply says that no one is notified from the chat. | `test_handoff_outside_the_demo_never_promises_a_reply_it_cannot_deliver` |

## Rules the code adds that the policy text does not state

- Order lookups need both the order number and the email on the order (`app/agent/graph.py`). A reply that offers a lookup by name, phone, address, or email alone, or says lookups are impossible, fails the source check (`verify.breaks_lookup_rule`).
- A return reason must be one of the store's reason codes, mapped to the customer's words.
- A change runs once, after a clear yes, with an idempotency key; a conditional yes never runs it.
- Every amount, date, time, and duration in a reply must appear in store records or in the policy text it cites (`verify.unsupported_claims`); numbers a reply needs, such as the refund after the fee or the time left, are calculated in code.
