"""Lire et reecrire un fichier projet Bome MIDI Translator Pro sans le casser.

LE FORMAT N'EST DOCUMENTE NULLE PART. Ce module est ce qu'on en a appris en editant
un projet reel de 88 translators, et chacune des regles ci-dessous a ete verifiee
contre Bome 1.9.2 -- en ouvrant le fichier produit et en constatant qu'il se charge.

    ; ------- Bome MIDI Translator: signed project file
    [Preset.6]
    Active=1
    Comments=…\\n…                 les retours a la ligne sont echappes
    Incoming0=MID3<Incoming …>     un translator par INDEX, reparti sur 4 cles
    Incoming1=Prfm<Incoming …>
    Name=Zones etat                le nom du PRESET, sans index
    Name0=CC112 -> ya P1 bas       le nom du translator 0
    Options0=Actv01Stop00OutO00StMa00000001ya=uu
    Outgoing0=None                 « None » = ce translator n'emet rien
    PresetSwitchIgnore=0
    [Signatures]
    Signature-1=…
    SignedBy-1=…

SEPT REGLES, toutes payees au moins une fois :

1.  UN TRANSLATOR EST REPARTI SUR QUATRE CLES indexees -- Name, Incoming, Outgoing,
    Options -- et non regroupe. Le manipuler impose de recoller les quatre.

2.  LES LIGNES SONT TRIEES ALPHABETIQUEMENT par nom de cle, index numerique ensuite :
    Active, Comments, Incoming0..N, Name, Name0..N, Options0..N, Outgoing0..N,
    PresetSwitchIgnore. Ecrire dans un autre ordre donne un fichier que Bome accepte
    mais reecrit entierement a la premiere sauvegarde, rendant illisible tout diff.

3.  LA NUMEROTATION DOIT ETRE CONTIGUE. Supprimer le translator 11 sur 39 impose de
    renumeroter les 27 suivants, sur les quatre cles a la fois.

4.  `Outgoing<N>=None` -- la chaine litterale -- est un translator qui n'emet rien.
    Il ne fait que poser des variables par ses regles. C'est une place libre, et
    c'est ce qui rend certaines refontes gratuites.

5.  LE CANAL EST 0-BASED : `<Channel num="11"/>` est le canal 12. `Type="Any"`
    a cote signifie « n'importe quel canal » et le numero devient sans objet. Ce
    piege a produit un faux diagnostic de regression le 2026-09-27.

6.  `Options` EST UN ENREGISTREMENT A LONGUEUR FIXE suivi des regles :
    `Actv01` actif · `Stop00` ne stoppe pas le traitement · `OutO00` options de
    sortie · `StMa` + HUIT chiffres hexadecimaux qui donnent le NOMBRE de regles,
    puis les regles concatenees sans separateur. Se tromper sur ce compte fait
    ignorer silencieusement les regles en trop.

7.  LE PREFIXE DE L'XML DIT LE TYPE D'ACTION : `MID3` pour MIDI, `Prfm` pour un
    Perform. Il n'est pas decoratif -- Bome s'en sert pour choisir son parseur.

ET DEUX CONTRAINTES QUI NE SONT PAS DANS LE FICHIER :

8.  BOME REECRIT LE FICHIER DEPUIS SA MEMOIRE a la sauvegarde. Editer pendant qu'il
    est ouvert, c'est perdre son travail au premier Cmd+S. D'ou `refuse_si_ouvert`.

9.  EDITER INVALIDE LA SIGNATURE. Le fichier reste utilisable dans MIDI Translator
    Pro, mais BomeBox et MT Player le refusent. Seul Bome sait signer : la parade
    est d'ouvrir le projet et de sauvegarder. Mesure sur un cas reel : une signature
    invalide a vecu cinq jours sans consequence visible sur un poste qui n'utilise
    que MT Pro.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

CHAMPS = ("Name", "Incoming", "Outgoing", "Options")
VIDE = "None"                      # un sortant absent s'ecrit ainsi


def bome_ouvert() -> bool:
    return subprocess.run(["pgrep", "-f", "MIDITranslatorPro"],
                          capture_output=True).returncode == 0


def refuse_si_ouvert() -> None:
    """A appeler avant toute ecriture. Voir la regle 8."""
    if bome_ouvert():
        raise SystemExit("Bome MIDI Translator est ouvert : il ecraserait le fichier "
                         "depuis sa memoire a la prochaine sauvegarde. Le fermer.")


@dataclass
class Translator:
    """Les quatre cles d'un meme index, recollees."""
    nom: str = ""
    incoming: str = ""
    outgoing: str = VIDE
    options: str = ""

    @property
    def emet(self) -> bool:
        return self.outgoing.strip() not in ("", VIDE)

    @property
    def canal(self) -> int | None:
        """1-16, ou None pour « n'importe quel canal ». Voir la regle 5."""
        m = re.search(r'<Channel num="(\d+)"(\s+Type="Any")?', self.incoming)
        if not m or m.group(2):
            return None
        return int(m.group(1)) + 1

    @property
    def numero(self) -> int | None:
        m = re.search(r'<Value1 num="(0x[0-9A-Fa-f]+|\d+)"', self.incoming)
        if not m:
            return None
        v = m.group(1)
        return int(v, 16) if v.startswith("0x") else int(v)

    def regles(self) -> list[str]:
        """Les regles declarees, telles qu'ecrites. Voir la regle 6."""
        m = re.search(r"StMa([0-9a-f]{8})(.*)$", self.options)
        return [] if not m else [m.group(2)]      # non decoupees : pas de separateur

    def pose_regles(self, regles: list[str], prefixe: str = "Actv01Stop00OutO00") -> None:
        """Reecrit Options avec le bon COMPTE -- l'erreur classique de la regle 6."""
        self.options = prefixe + ("StMa%08x%s" % (len(regles), "".join(regles))
                                  if regles else "")


@dataclass
class Preset:
    numero: int
    entete: dict[str, str] = field(default_factory=dict)   # Active, Comments, Name…
    translators: dict[int, Translator] = field(default_factory=dict)

    @property
    def actif(self) -> bool:
        return self.entete.get("Active", "1") == "1"

    @property
    def nom(self) -> str:
        return self.entete.get("Name", "")

    def retire(self, index: list[int]) -> None:
        """Retire des translators ET renumerote. Voir la regle 3."""
        for i in index:
            self.translators.pop(i, None)
        self.translators = {n: t for n, t in enumerate(
            self.translators[i] for i in sorted(self.translators))}

    def ajoute(self, t: Translator) -> int:
        i = max(self.translators, default=-1) + 1
        self.translators[i] = t
        return i

    def rend(self) -> str:
        """Le bloc texte, lignes triees comme Bome les trie. Voir la regle 2."""
        lignes = [f"{k}={v}" for k, v in self.entete.items()]
        for i in sorted(self.translators):
            t = self.translators[i]
            for champ, val in (("Name", t.nom), ("Incoming", t.incoming),
                               ("Outgoing", t.outgoing), ("Options", t.options)):
                if val or champ == "Outgoing":
                    lignes.append(f"{champ}{i}={val}")

        def rang(l: str):
            m = re.match(r"^([A-Za-z]+)(\d*)=", l)
            return (m.group(1), int(m.group(2)) if m.group(2) else -1)
        return f"[Preset.{self.numero}]\n" + "\n".join(sorted(lignes, key=rang))


class Projet:
    """Un fichier .bmtp, lu en objets et reecrit a l'identique quand rien ne change."""

    def __init__(self, chemin: Path):
        self.chemin = Path(chemin)
        self.texte = self.chemin.read_text(encoding="utf-8", errors="surrogateescape")
        self.presets: dict[int, Preset] = {}
        self._decoupe()

    def _decoupe(self) -> None:
        for m in re.finditer(r"^\[Preset\.(\d+)\]$", self.texte, re.M):
            num = int(m.group(1))
            fin = self.texte.find("\n[Preset.", m.end())
            corps = self.texte[m.end():fin if fin > 0 else len(self.texte)]
            p = Preset(num)
            for ligne in corps.split("\n"):
                mt = re.match(r"^(%s)(\d+)=(.*)$" % "|".join(CHAMPS), ligne)
                if mt:
                    t = p.translators.setdefault(int(mt.group(2)), Translator())
                    setattr(t, {"Name": "nom", "Incoming": "incoming",
                                "Outgoing": "outgoing", "Options": "options"}[mt.group(1)],
                            mt.group(3))
                elif "=" in ligne and not ligne.startswith("["):
                    k, _, v = ligne.partition("=")
                    p.entete[k] = v
            self.presets[num] = p

    def ecrit(self, preset: int) -> Path:
        """Reecrit UN preset en place, apres sauvegarde horodatee. Rend la sauvegarde."""
        refuse_si_ouvert()
        m = re.search(r"^\[Preset\.%d\]$" % preset, self.texte, re.M)
        if not m:
            raise LookupError(f"[Preset.{preset}] absent")
        fin = self.texte.find("\n[Preset.", m.end())
        sauve = self.chemin.with_name(
            self.chemin.name + time.strftime(".bak-%Y%m%d-%H%M%S"))
        shutil.copy2(self.chemin, sauve)
        neuf = self.texte[:m.start()] + self.presets[preset].rend() + \
            (self.texte[fin:] if fin > 0 else "\n")
        self.chemin.write_text(neuf, encoding="utf-8", errors="surrogateescape")
        self.texte = neuf
        return sauve

    def signature_valide(self) -> bool | None:
        """None si le fichier n'est pas signe. Voir la regle 9 -- indicatif seulement :
        seul Bome sait verifier, on ne peut que constater la PRESENCE d'une signature."""
        return None if "[Signatures]" not in self.texte else True
