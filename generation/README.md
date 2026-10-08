# Generating the conversations

The 250 core conversations are written by three models, about a third each, balanced so every
model writes an equal share of every failure type (`assignment.json`). Claude's share runs by
script; the ChatGPT and Gemini shares are pasted by hand.

## Your part: ChatGPT and Gemini (17 prompts each)

For each file `pack/chatgpt_01.md` … `pack/chatgpt_17.md`:

1. Open a **new chat** in ChatGPT for each file. A fresh chat stops earlier batches from
   influencing later ones.
2. Paste the whole file and send it.
3. Copy the whole reply (the JSON block is enough, but the full reply is fine: the importer
   finds the JSON inside it).
4. Save it as `raw/chatgpt/chatgpt_01.json` (same number as the prompt file).

Do the same for Gemini with `pack/gemini_*.md`, saving to `raw/gemini/gemini_01.json` and so on.

Once per model, write down the exact model version the app showed you (for example the name in
the model picker) in `raw/chatgpt/MODEL.txt` and `raw/gemini/MODEL.txt`. If you switch models
part-way through, write the version for those files in `raw/chatgpt/chatgpt_05.model.txt`
instead. This is recorded in `data/provenance/`, which the judge never sees.

If a reply is cut off ("continue generating"), ask it to continue and paste both parts together
into the one file, or just save what you have: the import reports anything missing.

## Then import and check

```bash
python -m safety_eval.importer          # validates everything saved so far
python -m safety_eval.generation redo   # writes prompts for anything missing or rejected
```

The importer rejects a conversation (it never repairs one) if it has the wrong shape (fewer than
4 or more than 10 turns, not alternating, not ending with a staff reply), if it was written by
the wrong model, or if its text leaks anything the judge must not see: fact or scenario IDs,
words like "planted" or "scenario", a model or company name, or a brand name. The full list of
reasons is in `reports/import_report.json`.

Redo prompts go to `pack/redo/`. Save their replies in the same model folder as
`chatgpt_redo_01.json` etc.; a later file replaces an earlier version of the same conversation.

## Claude's share and the natural-error set (scripted)

```bash
python -m safety_eval.generation run-claude   # 17 calls
python -m safety_eval.natural                 # 120 calls: asks for confirmation (over 100)
```

Both cache every response under `cache/`, so rerunning never repeats a call.
