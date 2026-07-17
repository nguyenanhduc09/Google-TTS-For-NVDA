CLD2 language detector
======================

This directory contains Windows x86 and x86-64 builds of Compact Language Detector 2
from the CLD2Owners/cld2 project:

https://github.com/CLD2Owners/cld2

CLD2 is licensed under the Apache License, Version 2.0. See LICENSE.txt
in this directory.

The add-on uses CLD2 only as a language detection helper for automatic
language profile selection. If the matching DLL cannot be loaded or CLD2
reports an unreliable result, the add-on falls back to its existing detector.
