// Crea dati/luoghi.tsv.gz e dati/paesi.json per i nomi dei luoghi (senza internet).
//
// Fonti: all-the-cities 3.1.0 (licenza MIT; dati GeoNames, CC BY 4.0: località con almeno
// 1000 abitanti) e i18n-iso-countries 7.14.0 (MIT) per i nomi dei paesi in italiano.
//   npm install all-the-cities@3.1.0 i18n-iso-countries@7.14.0
//   node strumenti/crea_luoghi.js <cartella di node_modules> dati/
// Righe del TSV: nome, lat, lon, paese (ISO), codice regione GeoNames, abitanti.
const path = require("path"), fs = require("fs"), zlib = require("zlib");
const [mods, out] = process.argv.slice(2);
const cities = require(path.join(mods, "all-the-cities"));
// solo comuni e località: niente quartieri (PPLX), località storiche, abbandonate o distrutte
const SKIP = new Set(["PPLX", "PPLH", "PPLQ", "PPLW", "PPLCH"]);
const lines = cities.filter(c => !SKIP.has(c.featureCode)).map(c => [c.name.replace(/\t/g, " "), c.loc.coordinates[1].toFixed(5),
  c.loc.coordinates[0].toFixed(5), c.country, c.adminCode || "", c.population || 0].join("\t"));
fs.writeFileSync(path.join(out, "luoghi.tsv.gz"), zlib.gzipSync(lines.join("\n"), {level: 9}));
const it = require(path.join(mods, "i18n-iso-countries", "langs", "it.json")).countries;
const paesi = {};
for (const [k, v] of Object.entries(it)) paesi[k] = Array.isArray(v) ? v[0] : v;
fs.writeFileSync(path.join(out, "paesi.json"), JSON.stringify(paesi));
console.log(`${lines.length} località, ${Object.keys(paesi).length} paesi`);
