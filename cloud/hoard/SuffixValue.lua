-- Hoard/SuffixValue.lua  (v0.54.1, SPEC.md sections 102 and 103)
-- THE VERSION DATA: what Hoard knows about the random suffixes ("of the Monkey") of gear, as plain data a follow-up can fill in WITHOUT touching any code.
-- Exposed through the Hoard namespace only (Hoard.SuffixData); no global. Loaded by Version.lua (Hoard.Version), which also takes tables passed to Hoard.Version.Load(tbl).
--
--   names[suffixID]  the suffix's words ("of the Monkey"): the owner's own saves, 4 Oct 2026 (HoardSniper's price memory + the item names in it). Hoard learns more at run time (HoardDB.suffixNames).
--   bonus[bonusID]   this client puts a replicate-scan link's random suffix in a BONUS id, not in the link's suffix field (HoardDB.debug.linkStats: 0 of 9,242 rows carried one), so Hoard's own scan
--                    saw every suffix as 0. The pairs below were joined by the suffix WORD ("Pathfinder Vest of the Boar" in Hoard's link samples = bonus 12725, "Cavalier Two-hander of the Boar" in
--                    the sniper's log = suffix 14305): each is verified by a name on BOTH sides. The two numberings are NOT a fixed offset (1579 to 1582 apart, and 1415 / 1419 for two), so this is a
--                    table, never arithmetic. Version.lua learns further pairs the same way (a word seen on both sides) and keeps them in HoardDB.suffixMap.
--   suffix[name]     per suffix (lower case): stats = what it gives, classes = who pays for it, tier = A (everyone wants it) to D (nobody), premium = what a version sells for against the item's other
--                    versions (1.00 = no premium), confidence = how sure the research was. v0.54.1: ALL 44 rows of the 4 Oct 2026 research table (tests/fixtures/suffix-values.csv, with its reasons), loaded
--                    as is; "of Fire Protection / Fire Resistance" style rows are one entry per name. Mostly judgement and modest by design (Tiger/Monkey 1.25, Eagle/Healing/Magic/Wraths 1.20, resistances
--                    0.85-0.90, Regeneration 0.80). Eleven names have no suffix id in `names` yet (Regeneration, Concentration, the Fire / Arcane Protection and the Frost / Nature / Shadow / Arcane
--                    Resistance wordings, Nature's Wrath, the Soldier): they apply as soon as Hoard learns an id for that name (HoardDB.suffixNames), no code change.
--   rules            patterns tried in order when a name has no entry above: { pattern (Lua, on the lower-case name), premium, why }
-- What Hoard actually uses (Hoard.Version.Premium) is this table's premium SHRUNK toward Hoard's own observations: (n x observed + 20 x table) / (n + 20), n = item-days, clamped to 0.70 .. 1.60 (SPEC 103).
-- Lua 5.1 only.

Hoard = Hoard or {}
Hoard.SuffixData = {
	version = 1,
	names = {
		[14299] = "of the Owl",
		[14300] = "of the Eagle",
		[14301] = "of the Whale",
		[14302] = "of the Bear",
		[14303] = "of the Gorilla",
		[14304] = "of the Falcon",
		[14305] = "of the Boar",
		[14306] = "of the Wolf",
		[14307] = "of the Tiger",
		[14308] = "of Agility",
		[14309] = "of Intellect",
		[14310] = "of Spirit",
		[14311] = "of Stamina",
		[14312] = "of Strength",
		[14313] = "of Power",
		[14314] = "of Magic",
		[14315] = "of Healing",
		[14316] = "of Defense",
		[14319] = "of Eluding",
		[14322] = "of Frost Protection",
		[14323] = "of Nature Protection",
		[14324] = "of Shadow Protection",
		[14325] = "of the Sorcerer",
		[14326] = "of the Elder",
		[14327] = "of the Channeler",
		[14328] = "of the Physician",
		[14329] = "of the Hierophant",
		[14330] = "of the Bandit",
		[14331] = "of the Beast",
		[14332] = "of the Champion",
		[14333] = "of the Defender",
		[14334] = "of the Knight",
		[14335] = "of Arcane Wrath",
		[14336] = "of Fiery Wrath",
		[14337] = "of Frozen Wrath",
		[14338] = "of Holy Wrath",
		[14340] = "of Shadow Wrath",
		[14455] = "of the Monkey",
	},
	bonus = {
		[12718] = 14300,   -- of the Eagle
		[12719] = 14301,   -- of the Whale
		[12720] = 14299,   -- of the Owl
		[12724] = 14304,   -- of the Falcon
		[12725] = 14305,   -- of the Boar
		[12726] = 14306,   -- of the Wolf
		[12727] = 14307,   -- of the Tiger
		[12729] = 14309,   -- of Intellect
		[12730] = 14310,   -- of Spirit
		[12731] = 14311,   -- of Stamina
		[12733] = 14313,   -- of Power
		[12735] = 14315,   -- of Healing
		[12736] = 14316,   -- of Defense
		[12745] = 14325,   -- of the Sorcerer
		[12747] = 14327,   -- of the Channeler
		[12750] = 14329,   -- of the Hierophant
		[12752] = 14331,   -- of the Beast
		[12755] = 14334,   -- of the Knight
		[12907] = 14326,   -- of the Elder
		[12913] = 14328,   -- of the Physician
	},
	suffix = {
		["of the tiger"] = { stats = "Strength + Agility", classes = { "Warrior", "Rogue", "Feral Druid", "Enhancement Shaman", "Hunter(melee)", "Ret Paladin(some)" }, tier = "A", premium = 1.25, confidence = "medium" },
		["of the monkey"] = { stats = "Agility + Stamina", classes = { "Rogue", "Hunter", "Warrior", "Feral Druid", "Enhancement Shaman" }, tier = "A", premium = 1.25, confidence = "medium" },
		["of the bear"] = { stats = "Strength + Stamina", classes = { "Warrior (all specs, esp levelling/Hardcore)", "Paladin", "Feral tank", "Shaman" }, tier = "B", premium = 1.15, confidence = "medium" },
		["of the gorilla"] = { stats = "Strength + Intellect", classes = { "Retribution Paladin", "Enhancement Shaman", "(very limited otherwise)" }, tier = "C", premium = 1.05, confidence = "low" },
		["of the boar"] = { stats = "Strength + Spirit", classes = { "nobody (sell or disenchant)" }, tier = "D", premium = 0.90, confidence = "medium" },
		["of the falcon"] = { stats = "Agility + Intellect", classes = { "Hunter", "Rogue (limited)" }, tier = "C", premium = 1.05, confidence = "low" },
		["of the wolf"] = { stats = "Agility + Spirit", classes = { "nobody (sell or disenchant)" }, tier = "D", premium = 0.90, confidence = "medium" },
		["of the eagle"] = { stats = "Stamina + Intellect", classes = { "Mage", "Warlock", "Priest", "Druid caster", "Shaman caster", "Paladin Holy", "tanks wanting mana" }, tier = "A", premium = 1.20, confidence = "medium" },
		["of the whale"] = { stats = "Stamina + Spirit", classes = { "Priest", "Holy Paladin", "Resto Druid (spirit regen)", "mostly neutral" }, tier = "C", premium = 1.00, confidence = "low" },
		["of the owl"] = { stats = "Intellect + Spirit", classes = { "Priest", "Holy Paladin", "Resto Druid/Shaman", "Mage (levelling mana)" }, tier = "B", premium = 1.15, confidence = "low" },
		["of strength"] = { stats = "Strength (single stat)", classes = { "Warrior", "Paladin", "Enhancement Shaman", "Feral" }, tier = "C", premium = 1.05, confidence = "low" },
		["of agility"] = { stats = "Agility (single stat)", classes = { "Rogue", "Hunter", "Feral Druid" }, tier = "C", premium = 1.05, confidence = "low" },
		["of stamina"] = { stats = "Stamina (single stat)", classes = { "Tanks", "Hardcore survivability buyers" }, tier = "C", premium = 1.00, confidence = "low" },
		["of intellect"] = { stats = "Intellect (single stat)", classes = { "Mage", "Warlock", "Priest", "any caster" }, tier = "B", premium = 1.10, confidence = "low" },
		["of spirit"] = { stats = "Spirit (single stat)", classes = { "Priest/healer regen only", "melee never" }, tier = "D", premium = 0.90, confidence = "medium" },
		["of power"] = { stats = "Attack Power (single, about 3x one pair-stat)", classes = { "Warrior", "Rogue", "Hunter", "Feral Druid", "Enh Shaman" }, tier = "C", premium = 1.05, confidence = "low" },
		["of defense"] = { stats = "Defense skill", classes = { "Protection Warrior/Paladin tanks only" }, tier = "D", premium = 0.95, confidence = "low" },
		["of healing"] = { stats = "+Healing (spell healing), about 1.5x a spell damage roll", classes = { "Priest", "Paladin", "Druid", "Shaman healers" }, tier = "A", premium = 1.20, confidence = "medium" },
		["of regeneration"] = { stats = "Health regen per 5 sec (Classic Era; not seen in Forever beta data)", classes = { "nobody" }, tier = "D", premium = 0.80, confidence = "low" },
		["of concentration"] = { stats = "Mana regen per 5 sec", classes = { "Healers at endgame only", "levelling casters no" }, tier = "D", premium = 0.90, confidence = "low" },
		["of eluding"] = { stats = "+1% dodge + Agility (Classic Era), dodge rating in Forever", classes = { "Rogue/tank niche only" }, tier = "D", premium = 0.85, confidence = "low" },
		["of fire protection"] = { stats = "Fire resistance (Forever Protection suffix also gives Stamina)", classes = { "raid-prep only (Molten Core, Ragnaros)", "not for levelling" }, tier = "D", premium = 0.85, confidence = "medium" },
		["of fire resistance"] = { stats = "Fire resistance (Forever Protection suffix also gives Stamina)", classes = { "raid-prep only (Molten Core, Ragnaros)", "not for levelling" }, tier = "D", premium = 0.85, confidence = "medium" },
		["of frost protection"] = { stats = "Frost resistance (Forever Protection also Stamina)", classes = { "raid-prep only (Sapphiron, Kel'Thuzad)", "PvP rare" }, tier = "D", premium = 0.85, confidence = "medium" },
		["of frost resistance"] = { stats = "Frost resistance (Forever Protection also Stamina)", classes = { "raid-prep only (Sapphiron, Kel'Thuzad)", "PvP rare" }, tier = "D", premium = 0.85, confidence = "medium" },
		["of nature protection"] = { stats = "Nature resistance (Forever Protection also Stamina)", classes = { "raid-prep only (AQ40 Huhuran, Naxx)", "spikes in-phase" }, tier = "D", premium = 0.90, confidence = "medium" },
		["of nature resistance"] = { stats = "Nature resistance (Forever Protection also Stamina)", classes = { "raid-prep only (AQ40 Huhuran, Naxx)", "spikes in-phase" }, tier = "D", premium = 0.90, confidence = "medium" },
		["of shadow protection"] = { stats = "Shadow resistance (Forever Protection also Stamina)", classes = { "raid-prep only (Naxx, Onyxia-era shadow fights)" }, tier = "D", premium = 0.85, confidence = "medium" },
		["of shadow resistance"] = { stats = "Shadow resistance (Forever Protection also Stamina)", classes = { "raid-prep only (Naxx, Onyxia-era shadow fights)" }, tier = "D", premium = 0.85, confidence = "medium" },
		["of arcane protection"] = { stats = "Arcane resistance (Forever Protection also Stamina)", classes = { "raid-prep only (AQ, some Naxx)", "near zero for levelling" }, tier = "D", premium = 0.85, confidence = "medium" },
		["of arcane resistance"] = { stats = "Arcane resistance (Forever Protection also Stamina)", classes = { "raid-prep only (AQ, some Naxx)", "near zero for levelling" }, tier = "D", premium = 0.85, confidence = "medium" },
		["of frozen wrath"] = { stats = "Frost spell damage (+34-36 on a level 55 cloth head, where Healing is +53-55)", classes = { "Frost Mages", "(Shadow/Fire casters no)" }, tier = "A", premium = 1.20, confidence = "medium" },
		["of shadow wrath"] = { stats = "Shadow spell damage", classes = { "Warlocks", "Shadow Priests" }, tier = "A", premium = 1.20, confidence = "medium" },
		["of fiery wrath"] = { stats = "Fire spell damage", classes = { "Fire Mages", "Warlock (Destruction fire)" }, tier = "B", premium = 1.15, confidence = "low" },
		["of arcane wrath"] = { stats = "Arcane spell damage", classes = { "Arcane Mages", "Balance Druids", "limited" }, tier = "C", premium = 1.10, confidence = "low" },
		["of holy wrath"] = { stats = "Holy spell damage (Forever new)", classes = { "Holy-damage casters: Paladin(Ret/Holy), Priest", "Forever Paladin population is very high" }, tier = "B", premium = 1.20, confidence = "low" },
		["of nature's wrath"] = { stats = "Nature spell damage (Forever beta; not in Classic Era greens)", classes = { "Balance Druid", "Elemental Shaman", "Restoration scaling" }, tier = "B", premium = 1.15, confidence = "low" },
		["of magic"] = { stats = "spell damage and healing together (new in Forever)", classes = { "any caster or healer: Mage", "Warlock", "Priest", "Paladin", "Shaman", "Druid" }, tier = "A", premium = 1.20, confidence = "medium" },
		["of the channeler"] = { stats = "Stamina + Spirit + spell damage and healing (new in Forever)", classes = { "Healers", "Priest", "Holy Paladin", "Resto Shaman/Druid" }, tier = "B", premium = 1.10, confidence = "low" },
		["of the sorcerer"] = { stats = "Stamina + Intellect + spell power (inferred from TBC naming; Forever stats unverified)", classes = { "Mage", "Warlock", "Shadow Priest", "Elemental Shaman", "Balance Druid" }, tier = "B", premium = 1.15, confidence = "low" },
		["of the elder"] = { stats = "Stamina + Intellect + Spirit (TBC convention; unverified in Forever)", classes = { "Priest", "Holy Paladin", "Resto Druid", "mixed casters" }, tier = "B", premium = 1.10, confidence = "low" },
		["of the physician"] = { stats = "Healing + Stamina or Spirit (TBC convention; unverified)", classes = { "Healers" }, tier = "B", premium = 1.10, confidence = "low" },
		["of the hierophant"] = { stats = "Healing + Intellect/Spirit (TBC convention; unverified)", classes = { "Healers" }, tier = "B", premium = 1.10, confidence = "low" },
		["of the bandit"] = { stats = "Agility + Stamina + attack power/crit (TBC convention; unverified)", classes = { "Rogue", "Hunter", "Feral Druid", "Enhancement Shaman" }, tier = "A", premium = 1.20, confidence = "low" },
		["of the beast"] = { stats = "Stamina + attack power/hit/crit (TBC convention; unverified)", classes = { "Hunter", "Rogue", "Warrior", "Enh Shaman" }, tier = "B", premium = 1.15, confidence = "low" },
		["of the champion"] = { stats = "Strength + Stamina + defense/dodge (TBC convention; unverified)", classes = { "Warrior", "Paladin", "tank specs", "levelling warriors" }, tier = "B", premium = 1.15, confidence = "low" },
		["of the defender"] = { stats = "Stamina + defense/block (TBC convention; unverified)", classes = { "Protection Warrior", "Protection Paladin" }, tier = "C", premium = 1.05, confidence = "low" },
		["of the knight"] = { stats = "Stamina + dodge + spell power? (retail table; unverified in Forever)", classes = { "Tank-paladin hybrids", "niche" }, tier = "D", premium = 0.95, confidence = "low" },
		["of the soldier"] = { stats = "Strength + Stamina + attack power/crit (TBC convention; not seen in Forever beta data)", classes = { "Warrior", "Paladin" }, tier = "C", premium = 1.05, confidence = "low" },
	},
	rules = {
		{ pattern = "protection$", premium = 0.85, why = "a resistance suffix: nobody pays extra for resistance" },
		{ pattern = "resistance$", premium = 0.85, why = "a resistance suffix: nobody pays extra for resistance" },
	},
}
