CLD2 language detector
======================

This directory contains Windows x86 and x86-64 builds of Compact Language Detector 2
from the CLD2Owners/cld2 project:

https://github.com/CLD2Owners/cld2

The bundled DLLs were rebuilt from CLD2Owners/cld2 commit
b56fa78a2fe44ac2851bae5bf4f4693a0644da7b with MinGW-w64. They export the
small C ABI used by this add-on:

- cld2_detect_language
- cld2_version

cld2.dll is the x86-64 compatibility fallback copy. cld2_x86.dll is used by
32-bit NVDA/Python and cld2_x64.dll is used by 64-bit NVDA/Python.

CLD2 is licensed under the Apache License, Version 2.0. See LICENSE.txt
in this directory.

The add-on uses CLD2 only as a language detection helper for automatic
language profile selection. If the matching DLL cannot be loaded or CLD2
reports an unreliable result, the add-on falls back to its existing detector.
