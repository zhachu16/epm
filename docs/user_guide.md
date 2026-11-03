# epm: user guide

## What is this?

epm is a replacement for Excel for financial models (LBO, DCF, budgets, three-statement).

In Excel, a formula looks like `=B14*(1+C3)-D9`. Nobody knows what B14 is. In epm, you name
things and write formulas with the names:

```
revenue = prev(revenue) * (1 + revenue_growth)
```

What that gets you:

- **No cell references.** No broken links, no "who moved row 40".
- **Every change is saved as a version.** You can see what changed and undo it.
- **An AI assistant can build and edit the model for you**, and you can edit it yourself in the web app. Both work on the same model.
- **Checks run all the time.** "Does the balance sheet balance?" is shown at the top of the screen.

> **This is an early test version.** There is no login. Anyone with a project's link can
> see and change it. **Do not put real or confidential numbers in it.**

---

## Step 1: Connect the AI assistant

You only do this once. You need this link:

```
https://epm-nm1g.onrender.com/mcp
```

Copy it. Then follow the steps for the assistant you use.

### Claude (claude.ai or the Claude desktop app)

1. Click your name (bottom-left), then **Settings**.
2. Click **Connectors**.
3. Click **Add custom connector**.
4. In **Name**, type `epm`.
5. In the URL box, paste the link above.
6. Click **Add**.
7. Start a new chat. Click the **+** (or tools) button in the message box and make sure
   **epm** is switched on.

### ChatGPT

You need a paid ChatGPT plan, and you have to switch on Developer mode first.

1. Click your name (bottom-left), then **Settings**.
2. Click **Apps** (older versions call it **Connectors**).
3. Click **Advanced settings** and switch on **Developer mode**.
4. Go back and click **Create** (or **Add connector**).
5. In **Name**, type `epm`.
6. In **MCP server URL**, paste the link above.
7. Set authentication to **None** (there is no login yet).
8. Tick the box saying you trust this app, then click **Create**.
9. Start a new chat. Click **+**, then **More**, and pick **epm** (in Developer mode).

Menu names move around. If you can't find something, look for "connectors" or "apps" in
settings.

### Check that it works

Type: **"List my epm projects."**

You should see a list that includes **Example: LBO**. If so, you're connected.

### What to ask it

- *"Make an editable copy of Example: LBO and call it Atlas."*
- *"In Atlas, change the interest rate to 5%. Show me the effect on equity IRR before you save."*
- *"Build me a simple 5-year DCF for a software company."*
- *"The balance sheet check is failing. Why?"*
- *"Undo that."*

The assistant shows you what will change first and saves only after you say so.

### Open the link it gives you

When the assistant creates a model, it replies with a **link** to it. **Click that link.**
It opens the model in the web app. That's all you need to do to get from chat to your model.
Whatever the assistant changes later shows up there within a few seconds.

---

## Step 2: Open the web app

**If the assistant gave you a link, click it and skip to Step 3.** You're already in your
model.

Otherwise, go to **https://epm-alpha.nommo.cloud**. You land on the **Projects** page. You'll see **Example: LBO**. It is read-only, so you
can't break it.

1. Click **Make an editable copy**.
2. Type a name, for example `Atlas`. Click OK.
3. The model opens. This is your own copy. Change whatever you like.

Other buttons on the Projects page:

- **New project**: an empty model.
- **Duplicate**: copy one of your projects.
- **Import .epm…**: upload a model file you downloaded earlier.
- **Delete**: hides a project. **Show recently deleted → Restore** brings it back (30 days).

---

## Step 3: Find your way around

When a model is open, the screen has these parts:

| Where | What is there |
| --- | --- |
| **Top bar** | The project name (click it to rename). The save status: **Saved · v3** means everything is saved. The checks and problems indicators. **Copy link** and **Export .epm**. |
| **Left side: Explorer** | Everything in the model, in folders: assumptions, metrics, tables, checks and so on. Use the search box to find anything by name or formula. The **+ New** menu adds new things. |
| **Middle** | Whatever you opened, in tabs. Also the dependency graph (a map of what feeds what). |
| **Right side: Inspector** | Click any item and it shows its value for each year, its unit, what it depends on, and what depends on it. |
| **Bottom** | Three tabs: **Versions** (history), **Files** (uploaded data), **Outputs** (saved results). |

The words you'll see:

- **Metric**: one named number or row of numbers. Revenue, interest rate, EBITDA.
- **Assumption**: a metric you type in (an input). Found under **Assumptions** in the explorer.
- **Table**: a formatted statement, like an income statement, made from metrics.
- **Check**: a test that should always pass, like "assets = liabilities + equity".
- **Analysis**: a sensitivity table or chart, like IRR by entry and exit multiple.

---

## Step 4: Change a number

Example: change the interest rate.

1. In the explorer, open **Assumptions → Financing → interest_rate**.
2. Change the value to `0.05` (that's 5%).
3. Click **Preview**.
4. Look at the **Changes** panel. It shows what you changed, and how the key outputs, like
   equity IRR, move. **Nothing is saved yet.**
5. Happy? Click **Save**. The top bar changes to **Saved · v2**.
6. Not happy? Click **Revert**.

If the model rejects your change (for example you typed a name that doesn't exist), it tells
you why and does not save.

### Undo

You can always go back. Open the **Versions** tab at the bottom.

- **View**: look at an old version (read-only).
- **Restore**: make an old version the current one again. Nothing is deleted. The restore is
  itself a new version.

---

## Step 5: Add something new

Click **+ New** in the explorer and pick what to add: **Metric**, **Table**, **Analysis**,
**Check**, **Function**, **Source**, **Circularity** or **Axis**. Start with **Metric**.

When you make a metric, fill in:

- **Name**: no spaces, for example `management_fee`.
- **Formula**: use other metrics' names, for example `revenue * management_fee_rate`.
  Or a **value** if it is an input.
- **Unit**: for example `USD m` or `%`.
- **Folder**: where it sits in the explorer, for example `Assumptions/Operations`.

Then **Preview**, then **Save**.

Two rules that keep models clean:

1. **Never type a number inside a formula.** Make it its own assumption. Write
   `revenue * management_fee_rate`, not `revenue * 0.01`.
2. **Balances that carry over use `prev()`.** Cash this year = last year's cash plus this
   year's cash flow: `prev(cash) + fcf`.

---

## Step 6: Edit the whole model as text (the JSON editor)

Behind the buttons and forms, a model is one text file called **model.json**. The JSON
editor lets you see and edit all of it at once. Use it when you want to make many changes
fast, or paste in something you got from the AI assistant.

### Open it

1. In the explorer on the left, scroll down to the **Files** folder.
2. Click **model.json**.

The whole model opens in a text editor in the middle of the screen.

### What you are looking at

It looks like this (shortened):

```json
{
  "settings": {
    "name": "Atlas",
    "key_outputs": ["equity_irr", "moic"]
  },
  "metrics": {
    "interest_rate": {
      "unit": "%",
      "folder": "Assumptions/Financing",
      "value": 0.04
    },
    "revenue": {
      "unit": "USD m",
      "folder": "Income statement",
      "formula": "prev(revenue) * (1 + revenue_growth)"
    }
  }
}
```

How to read it:

- **`metrics`** holds every metric. Each one has a name (`interest_rate`) and a definition
  in curly brackets `{ }`.
- A definition has a **`value`** (an input you type) or a **`formula`** (calculated from
  other metrics), plus **`unit`** and **`folder`**.
- **`settings`** has the model's name and its **`key_outputs`**: the results shown whenever
  you preview a change.
- Other sections (`tables`, `checks`, `analyses`, `axes`, `sources`) work the same way:
  a name, then a definition.

### Make an edit

Example: add a management fee.

1. In `"metrics"`, after the last metric, type a comma, then add:

   ```json
   "management_fee_rate": {
     "unit": "%",
     "folder": "Assumptions/Operations",
     "value": 0.01
   }
   ```

2. Click **Preview**. Read the errors or the Changes panel.
3. Click **Save**.

### JSON rules (the usual mistakes)

- Text goes in **double quotes** `"like this"`. Not single quotes.
- Put a **comma between items**, but **not after the last one** in a list.
- Every `{` needs a closing `}`. Every `[` needs a closing `]`.
- Percentages are decimals: 5% is `0.05`.

If you break the rules, the editor says `not valid JSON` and refuses to save. Your text
stays on screen so you can fix it.

### Save, autosave and safety

- **Preview** checks your edit and shows what would change. Saves nothing.
- **Save** saves it as a new version.
- **Autosave** is switched on by default: it saves a few seconds after you stop typing.
  Untick **autosave** at the top of the editor if you'd rather save by hand.
- A bad edit is **never saved**. The model must pass its checks first.
- Something went wrong anyway? Go to **Versions** and **Restore** the one before.

### One thing to avoid

**Don't rename a metric by changing its name in the JSON.** The editor sees that as
"delete the old one, create a new one", and every formula that used the old name breaks.
To rename, right-click the metric in the explorer and choose **Rename**. That updates every
formula for you.

### The other file: functions.py

Next to model.json is **functions.py**. Open it the same way. It holds your own
custom calculations, written in Python. You can ignore it unless you need something the
built-in formulas can't do.

---

## Share and download

- **Copy link** (top bar) copies the project's web address. Anyone with it can open the
  project (this is the early test version).
- **Export .epm** (top bar) downloads the whole model as one file. Upload it later with
  **Import .epm…** on the Projects page.

---

## Stuck?

| Problem | Do this |
| --- | --- |
| The assistant can't see epm | Check the connector is switched on in the chat (Step 1, last point). |
| **Not saved: changed elsewhere** | Someone, or the assistant, saved while you were editing. Reload the page, redo your edit, save again. |
| Red problem count at the top | Click it. It lists what is wrong and where. |
| I broke something | **Versions** tab, then **Restore** an earlier version. |
