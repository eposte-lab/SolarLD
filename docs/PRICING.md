# SolarLead — Costi, Margini, Piani Tariffari

> **Stato:** Ipotesi v1 — aprile 2026. Da rivedere dopo i primi 30 giorni di dati reali di un cliente pilota.
> Tutti i valori in **EUR al netto di IVA** salvo dove indicato. Cambio USD→EUR usato: **1,00 USD = 0,92 EUR**.

---

## 1. Stack tecnologico — servizi paganti

| Componente | Provider | Tipo costo |
|---|---|---|
| Database + Auth + Realtime | Supabase | Fisso piattaforma |
| Backend API + Worker | Railway | Fisso + uso |
| Dashboard + Portale lead | Vercel | Fisso piattaforma |
| AI scoring / creative / reply / WhatsApp | Anthropic Claude | **Variabile per lead** |
| Email outbound + inbound | Resend | Variabile (blocchi) |
| Verifica email | NeverBounce | **Variabile per lead** |
| WhatsApp Business | 360dialog + Meta | Fisso + variabile |
| Cartoline postali | Pixart | **Variabile per invio** |
| Mappe territori | Mapbox | Variabile (free tier ampio) |
| Dominio invio email | Cloudflare / Namecheap | Fisso annuale |

---

## 2. Costi FISSI di piattaforma (indipendenti dai clienti)

Questi li paghi tu anche con **zero clienti attivi**. Sono il "floor" del conto economico.

| Voce | Piano | Costo mensile | Note |
|---|---|---|---|
| Supabase | Pro | **€23** | $25/mese. Include DB fino a 8 GB, 100 GB traffico, 100k MAU auth, Realtime, Storage 100 GB |
| Railway | Pro | **€19** | $20/mese base + uso compute. Include 2 vCPU, 2 GB RAM, Redis addon condiviso |
| Vercel | Pro | **€18** | $20/mese. Serve per 2 progetti (dashboard + portal) + bandwidth, auto-scaling, preview envs |
| Sentry / log monitoring | Developer free | **€0** | Da valutare upgrade €24 quando passi >10 tenant |
| Dominio root SolarLead (.it / .com) | Registrar | **€1,5** | €18/anno ammortizzato |
| **TOTALE FISSO** | | **~€62/mese** | ≈ €744/anno |

**Soglia break-even fisso**: se il margine lordo medio per cliente è €200/mese → **basta 1 cliente** per coprire i costi fissi. Con 3+ clienti si genera utile.

---

## 3. Costi VARIABILI per cliente attivo (tenant)

Dipendono dai volumi di attività. Useremo un **"tenant medio"** come unità di stima:

### Profilo "tenant medio" ipotizzato

| Attività mensile | Quantità |
|---|---|
| Lead scannati (hunter_task) | 1.000 |
| Lead scorati (scoring agent) | 1.000 |
| Email outreach inviate (step 1+2+3) | 400 |
| Risposte email inbound processate | 60 |
| Sessioni WhatsApp conversazionali | 40 |
| Cartoline postali spedite | 15 |
| Verifiche NeverBounce | 400 |

### Costo per tenant medio

| Servizio | Volume | Unit cost | Costo/mese |
|---|---|---|---|
| Anthropic Claude Sonnet 4.5 — scoring (in: 600 tok, out: 150 tok × 1000 lead) | 750k tok | $3 in + $15 out / Mtok | ≈ **€3,8** |
| Anthropic Claude — creative generation (in: 1.2k, out: 800 × 400 email) | 800k tok | $3 in + $15 out / Mtok | ≈ **€6,3** |
| Anthropic Claude — reply classifier + sentiment (in: 400, out: 150 × 60 reply) | 33k tok | idem | ≈ **€0,3** |
| Anthropic Claude — WhatsApp ConversationAgent (in: 800, out: 400 × 40 sessioni × 5 turni) | 240k tok | idem | ≈ **€2,2** |
| Resend email (400 su 50k incluso nel piano da $20) | — | €18/mese tier | ≈ **€3** pro-quota |
| NeverBounce (400 verifiche × €0,0073) | 400 | $0,008/verifica | ≈ **€2,7** |
| 360dialog piano base + messaggi template (markup Meta) | ~60 messaggi | €39/mese + ~€0,05/msg | ≈ **€41** |
| Pixart cartoline A5 stampa+spedizione | 15 × €1,20 | Listino volume bassa tiratura | ≈ **€18** |
| Mapbox geocoding + map loads (sotto free tier) | — | — | ≈ **€0** |
| Hunter.io API (se attivo per enrich) | — | Piano base incluso | ≈ **€4** pro-quota |
| **TOTALE VARIABILE / TENANT / MESE** | | | **≈ €82** |

> **Nota critica:** 360dialog è il voce più pesante in fisso — è **per-tenant**, non piattaforma. Se serve un numero WhatsApp dedicato per ogni cliente, ogni tenant paga €39 di linea + messaggi. Questo spinge la soglia dei piani verso l'alto.
> Alternativa futura: **un unico numero SolarLead condiviso** con identità tenant nel messaggio (es. "Solare Napoli tramite SolarLead"). Costa meno ma riduce white-labeling.

---

## 4. Costo totale "all-in" per tenant

| | Voce | €/mese |
|---|---|---|
| + | Quota fissa piattaforma (€62 / N clienti) — **con 5 clienti** | 12 |
| + | Variabile per tenant medio | 82 |
| = | **Costo pieno per tenant** | **~€94** |

Con crescita clienti la quota fissa si diluisce: **con 20 clienti** scende a €3/tenant → costo pieno ~€85.

---

## 5. Setup iniziale (one-time per nuovo cliente)

### Cosa devi fare tecnicamente

| Attività | Tempo stimato | Note |
|---|---|---|
| Onboarding wizard + dati tenant | 30 min | Cliente compila da solo, tu supervisioni |
| Configurazione dominio email (DKIM/SPF/DMARC) su Resend | 1 h | Edit DNS del dominio del cliente |
| Setup numero WhatsApp 360dialog (verifica Meta) | 2-5 giorni (attesa Meta) | Asincrono, 30 min di lavoro effettivo |
| Upload brand assets (logo, colori, foto reali del cliente) | 30 min | Da materiali cliente |
| Configurazione territorio/i di lavoro (CAP, comuni) | 20 min | Dalla dashboard |
| Test invio email prova + rendering su Gmail/Outlook | 30 min | Smoke manuale |
| Primo scan territorio + review dei primi 10 lead con cliente | 1 h | Call formativa |
| Formazione cliente su dashboard (replies, conversations, lead portal) | 1,5 h | Call Zoom + registrazione video |
| Buffer imprevisti | 1 h | Sempre qualcosa salta fuori |
| **Totale tempo attivo** | **~6,5 h** | (escluso attesa Meta) |

### Costi vivi setup

| Voce | € |
|---|---|
| Crediti API iniziali (Claude + NeverBounce primo test) | 15 |
| 360dialog attivazione (setup una tantum) | 50 |
| Nulla per Resend/Vercel/Supabase (marginali) | 0 |
| **Totale costi vivi setup** | **~€65** |

### Prezzo commerciale setup fee proposto

Il tempo tuo vale. Un consulente/integratore tech fattura €60-100/h. Se vendi setup sottocosto svaluti l'intera offerta.

| Tier | Setup fee commerciale | Margine lordo setup |
|---|---|---|
| **Starter** | €497 | ~€432 |
| **Growth** | €997 (include call di strategia + copy B2B custom) | ~€830 |
| **Scale / Enterprise** | €1.997 (territori multipli, training team vendita cliente) | ~€1.700 |

**Importante**: il setup fee è anche **barriera a bassa serietà**. Chi non paga €500 una tantum non resterà su un abbonamento mensile.

---

## 6. Piani tariffari proposti

> Logica: prezzo mensile che copra ampiamente il costo pieno per tenant (€85-94) con **margine lordo 75-85%**. Questo lascia spazio a promozioni, churn iniziale e upgrade infrastruttura.

### Starter — **€297/mese** (+ €497 setup)

Per: installatore singolo, 1 zona di lavoro.

**Incluso:**
- 1 territorio attivo (CAP/comune singolo)
- Fino a **500 lead scannati/mese**
- Fino a **150 email outreach/mese** (step 1+2+3 inclusi)
- Reply AI + portal lead
- WhatsApp **numero condiviso SolarLead** (non dedicato)
- **10 cartoline postali/mese** incluse, poi €2/cad
- Dashboard per 1 utente
- Supporto email 48h

**Costo interno stimato:** ~€55/mese → **margine ~€242/mese (81%)**.

### Growth — **€697/mese** (+ €997 setup)

Per: installatore con 2-3 tecnici, zona provinciale.

**Incluso:**
- Fino a **3 territori attivi**
- Fino a **2.000 lead scannati/mese**
- Fino a **600 email outreach/mese**
- **Numero WhatsApp dedicato** (360dialog)
- **30 cartoline postali/mese** incluse, poi €1,80/cad
- Dashboard per 3 utenti
- A/B test esperimenti creatività
- CRM webhook outbound (push a Hubspot/Pipedrive)
- Supporto email 24h + call mensile strategica

**Costo interno stimato:** ~€160/mese → **margine ~€537/mese (77%)**.

### Scale — **€1.497/mese** (+ €1.997 setup)

Per: reti di installatori, franchising, aggregatori.

**Incluso:**
- Territori **illimitati**
- Fino a **5.000 lead/mese**
- Fino a **2.000 email outreach/mese**
- **2 numeri WhatsApp dedicati** (marketing + service)
- **100 cartoline postali/mese**, poi €1,50/cad
- Dashboard **utenti illimitati**
- Export dati CSV/API
- SLA 99,5% uptime
- Account manager dedicato, call bisettimanale
- Template email/WhatsApp custom scritti con il cliente

**Costo interno stimato:** ~€380/mese → **margine ~€1.117/mese (75%)**.

### Enterprise / Custom (su trattativa)

Per chi richiede > 5.000 lead/mese, white-label completo, hosting dedicato, integrazioni custom.
Partenza da **€3.000/mese**.

---

## 7. Proiezione economica a 12 mesi (scenario prudente)

| Mese | Clienti Starter | Clienti Growth | Clienti Scale | MRR | Costi fissi | Costi variabili | Utile lordo stimato |
|---|---|---|---|---|---|---|---|
| M1 (zio + 1 amico) | 2 | 0 | 0 | **€594** | €62 | €110 | **€422** |
| M3 | 4 | 1 | 0 | **€1.885** | €62 | €380 | **€1.443** |
| M6 | 6 | 2 | 0 | **€3.176** | €62 | €650 | **€2.464** |
| M9 | 8 | 3 | 1 | **€5.964** | €62 | €1.140 | **€4.762** |
| M12 | 10 | 5 | 1 | **€7.952** | €62 | €1.530 | **€6.360** |

**Una tantum setup fee**: con 16 attivazioni in 12 mesi × setup medio ~€750 = **~€12.000** extra.

**Ricavo annuo totale anno 1 (prudente):** **~€50.000 ARR + €12.000 one-time ≈ €62.000 lordi**.
**Utile lordo stimato anno 1:** **~€35-40.000** (prima di stipendio tuo, consulenza legale/fiscale, marketing).

---

## 8. Proposta commerciale per zio (script conversazione)

**Apertura:**
> "Ho costruito un software che per un installatore fotovoltaico trova contatti caldi nella sua zona, scrive email personalizzate alla proprietà di casa sua, risponde automaticamente alle risposte via WhatsApp con la sua voce aziendale, e gli consegna lead già filtrati e caldi nella sua dashboard. Tu ci metti il tuo brand e la tua zona. Io ci metto la piattaforma e il setup."

**Numeri da dire:**
- "Un installatore che oggi paga 3-5€ a lead crudo su Facebook Ads (ROAS alla giornata) con me paga **€297 al mese** e riceve fino a 500 lead/mese già arricchiti e contattati automaticamente. Costo effettivo per lead contattato: **€0,60**."
- "Se da 500 lead scannati ne trasformi 1-2 in impianto venduto (conservativo, 0,2-0,4% conversione), hai fatturato **€15-30k** dalla piattaforma nel primo mese. Il canone annuale è **€3.564**. Il ritorno è 10x al primo impianto."

**Offerta zio (family discount motivato):**
- Setup fee **€297** invece di €497 (scontato 40%)
- Prime 2 mensilità **€147** (poi €297 da M3)
- In cambio: testimonianza video + feedback settimanale sui primi 60 giorni per rifinire il prodotto

**Chiusura:**
- Prova tecnica in 30 min da mostrargli: scan del suo CAP, 10 lead veri, prima email generata con il suo brand.

---

## 9. Cose da rivedere dopo 30 giorni di uso reale

- [ ] Volume Claude reale (spesso più alto delle stime — rifinire system prompts per ridurre token)
- [ ] Tasso risposta WhatsApp (se <5% scendere a numero condiviso su tutti i tier)
- [ ] Costo cartoline Pixart reale con contratto volume (negoziare sotto €1/cad)
- [ ] Churn mensile (obiettivo <5%)
- [ ] CAC per canale (LinkedIn ads? passaparola installatori zona?)
- [ ] Rivedere tier Starter se margine scende <70% — alzare prezzo o tagliare limiti

---

## 10. Sanity check: cosa un concorrente italiano fa

Riferimento competitor (ricerca aprile 2026):

- **GreenLead.it** (lead solari generati): €5-8/lead venduto, nessun SaaS
- **Solarino CRM** (CRM verticale solare): €150-350/mese, no AI
- **LeadFotovoltaico.com**: €2.000 setup + €7/lead, no automazione outreach

**Il tuo positioning:** "Non vendo lead, vendo la **macchina che te li produce ogni mese**. Costa meno di 60 lead comprati e ne genera 500."
