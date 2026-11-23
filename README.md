# סל חכם 3D

השוואת סל קניות בין 9 רשתות השיווק הגדולות, לפי עיר.
Comparateur de panier de courses entre les 9 grandes chaînes, par ville.

- `index.html` – le site (carte 3D, liste fixe, classement, panier partagé, historique)
- `fetch_prices.py` – télécharge les vrais prix (loi sur la transparence des prix) et crée `prices/<ville>.json`
- `.github/workflows/prix.yml` – lance `fetch_prices.py` chaque jour et publie le site + les prix sur la branche `gh-pages`

Site : https://chlomo111-bit.github.io/sal-chacham/
