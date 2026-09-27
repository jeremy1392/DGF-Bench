#!/usr/bin/env python3
from __future__ import annotations
import getpass, os
from pathlib import Path

def merge_settings(text, settings):
    remaining=dict(settings); lines=[]
    for line in text.splitlines():
        key=line.split('=',1)[0].strip() if '=' in line and not line.lstrip().startswith('#') else None
        if key in settings:
            if key in remaining: lines.append(key+'='+remaining.pop(key))
        else: lines.append(line)
    lines.extend(key+'='+value for key,value in remaining.items())
    return '\n'.join(lines)+'\n'


def main():
    key=getpass.getpass("OpenRouter API key (input hidden): ").strip()
    if not key:
        raise SystemExit("No key entered")
    referer=input("HTTP-Referer [https://www.jeremycanale.com]: ").strip() or "https://www.jeremycanale.com"
    title=input("X-Title [DGF-Bench]: ").strip() or "DGF-Bench"
    p=Path.cwd()/".env"
    previous=p.read_text(encoding='utf-8') if p.exists() else ''
    p.write_text(merge_settings(previous,{'OPENROUTER_API_KEY':key,'OPENROUTER_HTTP_REFERER':referer,'OPENROUTER_X_TITLE':title}),encoding='utf-8')
    try: os.chmod(p,0o600)
    except Exception: pass
    print(f"Saved OpenRouter configuration to {p}. The file is gitignored.")

if __name__=="__main__": main()
