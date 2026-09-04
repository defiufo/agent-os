---
name: robinhood-rwa-addresses
description: "Look up Robinhood tokenized-stock (RWA) contract addresses and tickers. Use when: the user asks for the Robinhood token, on-chain contract address, ticker/symbol, or chain of a real-world stock or ETF (e.g. 'what is Apple's ticker', 'mã cổ phiếu Apple là gì', 'Robinhood contract address for Tesla', 'Microsoft token address'). Resolves a company name OR ticker to the on-chain token on Robinhood Chain. NOT for: live stock prices, trade execution, or non-Robinhood tokens. No API key needed."
homepage: https://robinhood.com
provenance:
  origin: agentos-original
  license: MIT
  maintained_by: AgentOS
publisher:
  id: robinhood
metadata:
  {
    "agentos":
      {
        "emoji": "🪶",
        "homepage": "https://robinhood.com",
        "requires": { "anyBins": ["python3", "python"] },
      },
  }
entrypoint:
  command: python3 {baseDir}/scripts/rwa_lookup.py
  args:
    - --query
    - "{{ with.query | default(inputs.user_message) }}"
    - --limit
    - "{{ with.limit | default(5) }}"
    - --timeout
    - "{{ with.timeout | default(10) }}"
  parse: json
  timeout: 20
---

# Robinhood RWA Contract Addresses

Resolve a real-world stock or ETF to its **Robinhood tokenized-asset (RWA)**
on-chain token: ticker/symbol, contract address, chain id, and decimals.

The lookup runs in two stages, and the second one is what makes the answer
trustworthy.

**1. Discovery** — rank candidates against the public CoinGecko token list for
Robinhood Chain (`https://tokens.coingecko.com/robinhood/all.json`, chainId
`4663`, no key). This is a third-party index, so it is a starting point, not
proof: it advertises assets Robinhood has announced but never deployed, and it
caps `name` at 60 characters, which chops the "• Robinhood Token" marker off
long listings like IBM and XLK.

**2. Verification** — confirm each candidate against Robinhood Chain itself over
JSON-RPC (`https://rpc.mainnet.chain.robinhood.com`, no key). Every genuine
Stock Token is a proxy pointing at Robinhood's shared EIP-1967 beacon
`0xe10b6f6b275de231345c20d14ab812db62151b00`. A permissionless impersonator
cannot forge that, and an undeployed listing has no contract at all. One
batched round-trip covers every candidate.

## Read `status` before you answer

Every match carries a `status`. **Never report an address without it.**

| `status` | Meaning | How to answer |
|---|---|---|
| `verified` | Beacon matches: a genuine, deployed Stock Token | Safe to give as the answer |
| `not-deployed` | Listed by the index, **no contract at that address** | Say the token is not live yet; warn against sending funds |
| `not-a-stock-token` | A contract exists but is not Robinhood's | Treat as an impersonator |
| `unverified` | The chain could not be reached, or `--no-verify` was passed | Say it is **unverified, not disproven** |

`unverified` never means "fake". If the RPC call failed, say the check could not
run — do not downgrade the token's legitimacy on the strength of a network
fault. A top-level `warning` field spells out whichever caveat applies.

## Community tokens are excluded on purpose

Robinhood Chain is permissionless, so the same list also carries community
tokens — and **some reuse a listed company's name and symbol verbatim**. Two
entries are called "GameStop" with symbol `GME`:

- `0x1b0e319c6a659f002271b69db8a7df2f911c153e` — the real Stock Token (`verified`)
- `0x7e86381a763f0ecca2bdf27c54eac403ddd48123` — an impersonator (`not-a-stock-token`)

The lookup returns **Stock Tokens only** by default. Pass `--include-community`
to widen the search; genuine Stock Tokens still rank above impersonators, and
the beacon check labels each one.

## When the user asks

Questions this skill answers — in any language:

- "What is Apple's ticker / stock symbol?" → `AAPL`
- "mã cổ phiếu Apple là gì" → `AAPL`, contract `0xaf3d…93f9`
- "Robinhood contract address for Tesla" → the `TSLA` token address
- "What's the on-chain address of Microsoft on Robinhood?" → the `MSFT` address

Answer with the **symbol** and the **contract address**, and mention it lives on
Robinhood Chain (chainId 4663). Include the address verbatim.

**On the Web chat channel, pipe the lookup through `rwa_cards.py`** — one
command, see [Show the results as cards](#show-the-results-as-cards-in-web-chat).
The card grid is published for you; you do not call `publish_artifact`. Do
**not** hand-write a markdown table of the matches: a 42-character address turns
one into a horizontal scroll. Answer in prose and let the grid carry the data.

## Run it

```bash
# By company name
python3 {baseDir}/scripts/rwa_lookup.py --query "Apple"

# By ticker
python3 {baseDir}/scripts/rwa_lookup.py --query "AAPL"

# Limit matches
python3 {baseDir}/scripts/rwa_lookup.py --query "Tesla" --limit 1

# Include non-stock community tokens (off by default)
python3 {baseDir}/scripts/rwa_lookup.py --query "GME" --include-community

# Skip the on-chain check (offline; every match comes back "unverified")
python3 {baseDir}/scripts/rwa_lookup.py --query "Apple" --no-verify

# Point at a different Robinhood Chain node
python3 {baseDir}/scripts/rwa_lookup.py --query "Apple" --rpc-url https://…
```

## Show the results as cards in Web chat

**Required on the Web chat channel whenever the lookup returned any match.**
Render the matches as a card grid rather than a markdown table — a contract
address is 42 characters and would force a table into a horizontal scroll.

Every run writes a card grid next to the JSON (`<SYMBOL>.cards.json`) and
`exec_command` publishes it. **There is nothing for you to do** — no extra
command, no flag, no `publish_artifact` call. The chat draws one card per match
with the token logo, a status badge, and a copy button on the address.

So: answer in prose and let the grid carry the addresses. Do **not** also
hand-write a markdown table of the matches — a 42-character address turns one
into a horizontal scroll.

Two things would silently break this, so do neither:

- **Never append `2>/dev/null`** (or any stderr redirect) to the command. The
  publish marker travels on stderr; discarding it discards the grid.
- **Never pass `--no-cards`.** It exists for humans running the script by hand.

`--cards FILE` picks the filename if you need a specific one.
`scripts/rwa_cards.py` builds the same payload from the JSON on stdin.

Keep `--output` a bare filename as shown. Scripts run with the workspace as
their working directory, and `publish_artifact` only accepts files inside that
workspace — writing to `/tmp` or any other absolute path outside it makes the
publish fail.

The badge colour comes from `status`: `verified` reads green, `not-deployed`
amber, `not-a-stock-token` red, `unverified` grey. The lookup's `warning` is
carried into the grid subtitle, so the caveat travels with the addresses.
Still say the caveat in your own answer too — the card grid supplements the
reply, it does not replace it.

Use the cards only when there is something to show; on an empty result the
script exits non-zero and you should answer in text instead.

### Output (JSON)

```json
{
  "query": "Apple",
  "source": "https://tokens.coingecko.com/robinhood/all.json",
  "rpc": "https://rpc.mainnet.chain.robinhood.com",
  "beacon": "0xe10b6f6b275de231345c20d14ab812db62151b00",
  "total_tokens": 683,
  "stock_tokens": 243,
  "matches": [
    {
      "name": "Apple",
      "symbol": "AAPL",
      "address": "0xaf3d76f1834a1d425780943c99ea8a608f8a93f9",
      "chainId": 4663,
      "decimals": 18,
      "isStockToken": true,
      "logoURI": "https://assets.coingecko.com/...",
      "status": "verified",
      "beacon": "0xe10b6f6b275de231345c20d14ab812db62151b00"
    }
  ]
}
```

An undeployed listing looks like this — the address is reported, but flagged:

```json
{
  "matches": [{ "symbol": "JPM", "status": "not-deployed", "beacon": null }],
  "warning": "listed by the token index but not deployed on Robinhood Chain: …"
}
```

`total_tokens` counts the whole list; `stock_tokens` counts the entries whose
*name* looks like a tokenized stock or ETF. Both move as Robinhood lists new
assets and the community deploys more tokens — read them from the payload,
never assume. Note that `stock_tokens` is a name-based hint over the whole
list; only a match's own `status` reflects the chain.

## Matching rules

The lookup ranks matches so the best answer comes first:

1. Exact ticker match (`AAPL`) — highest.
2. Exact company-name match (`Apple`).
3. Whole-word name match, then substring on name/symbol.

If nothing matches, `matches` is empty and an `error` explains why. On a network
failure the script still returns JSON with an `error` field (never crashes).

## Notes

- No API key required; both the token list and the RPC node are public.
- Addresses are on **Robinhood Chain** (chainId `4663`) — not Ethereum mainnet.
- Verification adds one batched RPC round-trip (~0.5s). Use `--no-verify` only
  when the chain is unreachable, and say so in the answer.
- This skill resolves and verifies addresses. For live price, holdings, supply,
  or the ERC-8056 corporate-action multiplier, use **`robinhood-chain-stocks`**.
