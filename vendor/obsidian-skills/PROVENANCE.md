# Provenance

Vendored from [kepano/obsidian-skills](https://github.com/kepano/obsidian-skills)
at commit `fa1e131a014576ff8f8919f191a7ca8d8fded39b`.

Only the `obsidian-markdown` skill is vendored here. It was the source for
the historical bundled primer; `get_vault_guide` now returns explicitly mapped
vault files and does not load this vendored skill.
The other skills in upstream (`obsidian-bases`, `json-canvas`,
`obsidian-cli`, `defuddle`) were not vendored — pull them in if/when the
server grows to handle `.base` / `.canvas` files or shells out to the
Obsidian CLI.

Licensed MIT — see `LICENSE`.

To refresh:

```sh
git -C /tmp clone --depth 1 https://github.com/kepano/obsidian-skills.git
cp -R /tmp/obsidian-skills/skills/obsidian-markdown vendor/obsidian-skills/skills/
cp /tmp/obsidian-skills/LICENSE /tmp/obsidian-skills/README.md vendor/obsidian-skills/
# then update the commit SHA above
```
