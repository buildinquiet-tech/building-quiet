# Products

Source for the free Gumroad downloads. One folder per product:

```
products/<slug>/
  product.json   name, Gumroad URL, and how the shipped file is built
  cover.png      the Gumroad cover
  src/           exactly what ships
```

Shipped files are cut from a tag, never from the working tree:

```bash
git tag the-queue-kit-v2            # after changing products/the-queue-kit/src
python3 scripts/build_product.py the-queue-kit
# -> dist/queue-kit.zip, then upload that file to Gumroad
```

The same tag always builds the same bytes. `--check <old file>` compares a build against what is already on Gumroad, so a re-upload only happens when something actually changed.
