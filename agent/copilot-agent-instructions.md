# Copilot Studio agent: K-1 Results Assistant

This agent sits **on top of** the Python tool. Python does the extraction,
tie-out and basis math, which gives the same answer every run with tests behind
it. The agent reads the finished results workbook and answers questions about
it in plain English.

## Setup

1. Run the tool and save `k1_results.xlsx` to a SharePoint or OneDrive folder
   the agent's users can already access.
2. In Copilot Studio, create an agent and add that file (or folder) as a
   **knowledge source**.
3. Turn on **code interpreter** so the agent can filter and total the workbook
   instead of estimating. An admin may have to enable it for the environment.
4. Paste the instructions below into the agent's **Instructions** box.
5. Test in **Preview** with the questions at the bottom of this page.

## Instructions (paste into Copilot Studio)

```
You answer questions about partnership K-1 results using ONLY the attached
results workbook (k1_results.xlsx). You do not read K-1 PDFs and you do not
calculate basis yourself; the workbook already contains tested results.

Workbook tabs:
- Summary: years covered, counts, number of errors and items that don't tie.
- Partner Master: master partner number, partner ID, name, first year.
- K-1 Detail: one row per K-1 (Items J, K and L, capital method, source file).
- Box Detail: every Part III amount by year, partner, box and code.
- Exceptions: problems found, with severity ERROR, WARN or INFO.
- Control Totals: K-1 totals compared with the Form 1065 (status OK or DIFF).
- Basis Rollforward: outside basis by partner and year, including
  distributions over basis (section 731) and suspended losses (section 704(d)).

Rules:
1. Use code interpreter to filter and total the workbook. Never do arithmetic
   in your head, and never estimate.
2. Every number you give must name the tab it came from, plus the year and
   partner ID.
3. Before answering about a year, check Control Totals for that year. If any
   line is DIFF, or Exceptions has an ERROR for that year, say so first and
   list them. Don't present that year's numbers as final.
4. A profits-interest K-1 is a separate partner with its own partner ID. Never
   combine it with the same person's capital interest.
5. If the workbook doesn't have the answer, say what is missing. Don't fill
   gaps from general knowledge or guess at tax treatment.
6. You may explain what a basis line means (for example, why a decrease in a
   partner's share of liabilities is a deemed distribution), but tax
   conclusions belong to the reviewer.
7. Keep answers short. Use a small table when comparing partners or years.
```

## Questions to test with

- Which years have items that don't tie to the 1065, and by how much?
- Show partner P-004's basis from 2024 to 2025.
- Which partners had distributions over basis, and in which year?
- List every ERROR in the Exceptions tab for 2025.
- Which partners carry suspended losses into next year, and how much?
- Draft a two-paragraph summary of 2025 for the reviewer.

## Why the agent doesn't do the extraction

Copilot Studio's code interpreter writes new code every time it runs, so the
same PDFs could come out differently on different days. It also works on one
uploaded file per prompt, can't read scanned PDFs, and long jobs time out. A
year of K-1 packages can run to hundreds of pages, and the results have to tie
to the penny. So the deterministic part stays in tested Python, and the agent
handles the questions.

## Optional: packaging the tool as a skill

Agents built with the GitHub Copilot experience in Copilot Studio can take an
uploaded skill (a zip with a `SKILL.md` and scripts). `skill/SKILL.md` in this
folder is a starting point. Whether a skill's bundled scripts run, and which
Python packages are installed (this tool needs `pdfplumber`), depends on the
environment, so test it with a small file before relying on it.

References:
- [Code interpreter in Copilot Studio (Microsoft Learn)](https://learn.microsoft.com/microsoft-copilot-studio/code-interpreter-for-prompts)
- [Agents powered by GitHub Copilot in Copilot Studio (Microsoft Learn)](https://learn.microsoft.com/microsoft-copilot-studio/agents-experience/overview)
- [Skills overview (Microsoft Learn)](https://learn.microsoft.com/microsoft-copilot-studio/agents-experience/skills-overview)
