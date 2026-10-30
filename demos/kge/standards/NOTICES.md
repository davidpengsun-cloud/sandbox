# Notices for the preloaded reference standards

`preload.yaml`, beside this file, holds a few identifiers (IRIs), labels and short definitions from the external
standards below, which the platform loads into every client's graph as reference concepts. Each standard's own
licence governs what is taken from it; the platform's licence does not. This file carries the notice each licence
asks for, and ships with `preload.yaml` in the image. What each standard's terms allow was read on 2026-10-19.

| Standard | Version | Licence | What ships |
|---|---|---|---|
| FIBO | master_2026Q2 | MIT | IRIs, labels and definitions, verbatim |
| BIAN | 14.0.0 | Apache-2.0 | a locator URL, a label and a definition, verbatim |
| schema.org | 30.1 | CC BY-SA 3.0 | IRIs and labels; definitions in our own words |
| SKOS | W3C Recommendation 2009-08-18 | W3C Document License | an IRI, a label and a definition, verbatim |
| PROV-O | W3C Recommendation 2013-04-30 | W3C Document License | IRIs, labels and definitions, verbatim |
| DCAT 3 | W3C Recommendation 2024-08-22 | W3C Software and Document License | an IRI, a label and a definition, verbatim |
| DPV and its pd extension | 2.3 | W3C Document License | IRIs, labels and definitions, verbatim |
| DCMI Metadata Terms | issued 2020-01-20 | CC BY 4.0 | IRIs, labels and definitions, verbatim |
| Apache Ossie | 0.2.0.dev0 (draft) | Apache-2.0 | its name and version only: a format, no concept |

ISO 20022 is not preloaded: its terms do not plainly allow its text to ship under the platform's licence.

## FIBO

The Financial Industry Business Ontology, from the EDM Council, adopted as a standard by the Object Management Group:
https://spec.edmcouncil.org/fibo/ (release master_2026Q2). Each ontology the concepts come from licenses itself under
MIT and states its own copyright, which follows as each file gives it:

| Ontology | Copyright |
|---|---|
| FND/Accounting/CurrencyAmount | Copyright (c) 2013-2025 EDM Council, Inc.; Copyright (c) 2013-2025 Object Management Group, Inc. |
| FND/Accounting/ISO4217-CurrencyCodes | Copyright (c) 2015-2025 EDM Council, Inc.; Copyright (c) 2015-2025 Object Management Group, Inc.; Copyright (c) 2015-2025 Thematix Partners LLC; Copyright (c) 2022-2025 agnos.ai UK Ltd. |
| FND/Accounting/CashFlows | Copyright (c) 2025 EDM Council, Inc.; Copyright (c) 2025 Object Management Group, Inc. |
| FND/Arrangements/Assessments | Copyright (c) 2019-2024 EDM Council, Inc.; Copyright (c) 2019-2024 Object Management Group, Inc. |
| FND/Arrangements/Reporting | Copyright (c) 2018-2025 EDM Council, Inc.; Copyright (c) 2018-2025 Object Management Group, Inc. |
| FND/DatesAndTimes/FinancialDates | Copyright (c) 2014-2025 EDM Council, Inc.; Copyright (c) 2014-2025 Object Management Group, Inc. |
| FND/Law/LegalCapacity | Copyright (c) 2013-2025 EDM Council, Inc.; Copyright (c) 2013-2025 Object Management Group, Inc. |
| FND/OwnershipAndControl/Ownership | Copyright (c) 2013-2025 Object Management Group, Inc.; Copyright (c) 2013-2026 EDM Association dba EDM Council, Inc. |
| FBC/ProductsAndServices/ClientsAndAccounts | Copyright (c) 2015-2025 Object Management Group, Inc.; Copyright (c) 2015-2026 EDM Association dba EDM Council, Inc. |

The repository's licence, which the ontologies' own MIT licence statements refer to:

```text
The MIT License (MIT)

Copyright (c) 2020 Enterprise Data Management Council

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

## BIAN

The BIAN Service Landscape's semantic APIs, release 14.0.0, by the Banking Industry Architecture Network, from
https://github.com/bian-official/public (the Investment Portfolio Management service domain's description). Licensed
under the Apache License, Version 2.0, whose text follows. No NOTICE file was found with the release.

```text
Apache License
                           Version 2.0, January 2004
                        https://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

   1. Definitions.

      "License" shall mean the terms and conditions for use, reproduction,
      and distribution as defined by Sections 1 through 9 of this document.

      "Licensor" shall mean the copyright owner or entity authorized by
      the copyright owner that is granting the License.

      "Legal Entity" shall mean the union of the acting entity and all
      other entities that control, are controlled by, or are under common
      control with that entity. For the purposes of this definition,
      "control" means (i) the power, direct or indirect, to cause the
      direction or management of such entity, whether by contract or
      otherwise, or (ii) ownership of fifty percent (50%) or more of the
      outstanding shares, or (iii) beneficial ownership of such entity.

      "You" (or "Your") shall mean an individual or Legal Entity
      exercising permissions granted by this License.

      "Source" form shall mean the preferred form for making modifications,
      including but not limited to software source code, documentation
      source, and configuration files.

      "Object" form shall mean any form resulting from mechanical
      transformation or translation of a Source form, including but
      not limited to compiled object code, generated documentation,
      and conversions to other media types.

      "Work" shall mean the work of authorship, whether in Source or
      Object form, made available under the License, as indicated by a
      copyright notice that is included in or attached to the work
      (an example is provided in the Appendix below).

      "Derivative Works" shall mean any work, whether in Source or Object
      form, that is based on (or derived from) the Work and for which the
      editorial revisions, annotations, elaborations, or other modifications
      represent, as a whole, an original work of authorship. For the purposes
      of this License, Derivative Works shall not include works that remain
      separable from, or merely link (or bind by name) to the interfaces of,
      the Work and Derivative Works thereof.

      "Contribution" shall mean any work of authorship, including
      the original version of the Work and any modifications or additions
      to that Work or Derivative Works thereof, that is intentionally
      submitted to Licensor for inclusion in the Work by the copyright owner
      or by an individual or Legal Entity authorized to submit on behalf of
      the copyright owner. For the purposes of this definition, "submitted"
      means any form of electronic, verbal, or written communication sent
      to the Licensor or its representatives, including but not limited to
      communication on electronic mailing lists, source code control systems,
      and issue tracking systems that are managed by, or on behalf of, the
      Licensor for the purpose of discussing and improving the Work, but
      excluding communication that is conspicuously marked or otherwise
      designated in writing by the copyright owner as "Not a Contribution."

      "Contributor" shall mean Licensor and any individual or Legal Entity
      on behalf of whom a Contribution has been received by Licensor and
      subsequently incorporated within the Work.

   2. Grant of Copyright License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      copyright license to reproduce, prepare Derivative Works of,
      publicly display, publicly perform, sublicense, and distribute the
      Work and such Derivative Works in Source or Object form.

   3. Grant of Patent License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      (except as stated in this section) patent license to make, have made,
      use, offer to sell, sell, import, and otherwise transfer the Work,
      where such license applies only to those patent claims licensable
      by such Contributor that are necessarily infringed by their
      Contribution(s) alone or by combination of their Contribution(s)
      with the Work to which such Contribution(s) was submitted. If You
      institute patent litigation against any entity (including a
      cross-claim or counterclaim in a lawsuit) alleging that the Work
      or a Contribution incorporated within the Work constitutes direct
      or contributory patent infringement, then any patent licenses
      granted to You under this License for that Work shall terminate
      as of the date such litigation is filed.

   4. Redistribution. You may reproduce and distribute copies of the
      Work or Derivative Works thereof in any medium, with or without
      modifications, and in Source or Object form, provided that You
      meet the following conditions:

      (a) You must give any other recipients of the Work or
          Derivative Works a copy of this License; and

      (b) You must cause any modified files to carry prominent notices
          stating that You changed the files; and

      (c) You must retain, in the Source form of any Derivative Works
          that You distribute, all copyright, patent, trademark, and
          attribution notices from the Source form of the Work,
          excluding those notices that do not pertain to any part of
          the Derivative Works; and

      (d) If the Work includes a "NOTICE" text file as part of its
          distribution, then any Derivative Works that You distribute must
          include a readable copy of the attribution notices contained
          within such NOTICE file, excluding those notices that do not
          pertain to any part of the Derivative Works, in at least one
          of the following places: within a NOTICE text file distributed
          as part of the Derivative Works; within the Source form or
          documentation, if provided along with the Derivative Works; or,
          within a display generated by the Derivative Works, if and
          wherever such third-party notices normally appear. The contents
          of the NOTICE file are for informational purposes only and
          do not modify the License. You may add Your own attribution
          notices within Derivative Works that You distribute, alongside
          or as an addendum to the NOTICE text from the Work, provided
          that such additional attribution notices cannot be construed
          as modifying the License.

      You may add Your own copyright statement to Your modifications and
      may provide additional or different license terms and conditions
      for use, reproduction, or distribution of Your modifications, or
      for any such Derivative Works as a whole, provided Your use,
      reproduction, and distribution of the Work otherwise complies with
      the conditions stated in this License.

   5. Submission of Contributions. Unless You explicitly state otherwise,
      any Contribution intentionally submitted for inclusion in the Work
      by You to the Licensor shall be under the terms and conditions of
      this License, without any additional terms or conditions.
      Notwithstanding the above, nothing herein shall supersede or modify
      the terms of any separate license agreement you may have executed
      with Licensor regarding such Contributions.

   6. Trademarks. This License does not grant permission to use the trade
      names, trademarks, service marks, or product names of the Licensor,
      except as required for reasonable and customary use in describing the
      origin of the Work and reproducing the content of the NOTICE file.

   7. Disclaimer of Warranty. Unless required by applicable law or
      agreed to in writing, Licensor provides the Work (and each
      Contributor provides its Contributions) on an "AS IS" BASIS,
      WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
      implied, including, without limitation, any warranties or conditions
      of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A
      PARTICULAR PURPOSE. You are solely responsible for determining the
      appropriateness of using or redistributing the Work and assume any
      risks associated with Your exercise of permissions under this License.

   8. Limitation of Liability. In no event and under no legal theory,
      whether in tort (including negligence), contract, or otherwise,
      unless required by applicable law (such as deliberate and grossly
      negligent acts) or agreed to in writing, shall any Contributor be
      liable to You for damages, including any direct, indirect, special,
      incidental, or consequential damages of any character arising as a
      result of this License or out of the use or inability to use the
      Work (including but not limited to damages for loss of goodwill,
      work stoppage, computer failure or malfunction, or any and all
      other commercial damages or losses), even if such Contributor
      has been advised of the possibility of such damages.

   9. Accepting Warranty or Additional Liability. While redistributing
      the Work or Derivative Works thereof, You may choose to offer,
      and charge a fee for, acceptance of support, warranty, indemnity,
      or other liability obligations and/or rights consistent with this
      License. However, in accepting such obligations, You may act only
      on Your own behalf and on Your sole responsibility, not on behalf
      of any other Contributor, and only if You agree to indemnify,
      defend, and hold each Contributor harmless for any liability
      incurred by, or claims asserted against, such Contributor by reason
      of your accepting any such warranty or additional liability.

   END OF TERMS AND CONDITIONS

   APPENDIX: How to apply the Apache License to your work.

      To apply the Apache License to your work, attach the following
      boilerplate notice, with the fields enclosed by brackets "[]"
      replaced with your own identifying information. (Don't include
      the brackets!)  The text should be enclosed in the appropriate
      comment syntax for the file format. We also recommend that a
      file or class name and description of purpose be included on the
      same "printed page" as the copyright notice for easier
      identification within third-party archives.

   Copyright [yyyy] [name of copyright owner]

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       https://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
```

## schema.org

Schema.org vocabulary, version 30.1, https://schema.org/, licensed under the Creative Commons Attribution-ShareAlike
3.0 licence (https://schema.org/docs/terms.html and https://creativecommons.org/licenses/by-sa/3.0/). Only its IRIs
and labels are used; the definitions beside them are our own words, not schema.org's text.

## SKOS and PROV-O

SKOS Simple Knowledge Organization System Reference, W3C Recommendation 18 August 2009,
https://www.w3.org/TR/2009/REC-skos-reference-20090818/: Copyright © 2009 W3C® (MIT, ERCIM, Keio), All Rights Reserved.

PROV-O: The PROV Ontology, W3C Recommendation 30 April 2013, https://www.w3.org/TR/2013/REC-prov-o-20130430/: Copyright ©
2011-2013 W3C® (MIT, ERCIM, Keio, Beihang), All Rights Reserved.

Both are used under the W3C Document License, https://www.w3.org/copyright/document-license-2023/.

## DCAT

Data Catalog Vocabulary (DCAT) - Version 3, W3C Recommendation 22 August 2024, https://www.w3.org/TR/vocab-dcat-3/:
Copyright © 2024 World Wide Web Consortium. Used under the W3C Software and Document License, https://www.w3.org/copyright/software-license-2023/.

## DPV

Data Privacy Vocabulary (DPV) 2.3 and its Personal Data extension (pd), https://w3id.org/dpv and
https://w3id.org/dpv/pd, published by the W3C Data Privacy Vocabularies and Controls Community Group (a Community Group,
not a W3C Recommendation). Used under the W3C Document License, https://www.w3.org/copyright/document-license-2023/,
which its files declare as their licence. Its files state no copyright line of their own; the notice for a document
without one is in the form that licence gives: Copyright © 2026 World Wide Web Consortium,
https://www.w3.org/copyright/document-license-2023/ (the form was not verified against the licence's text when this
file was written).

## DCMI Metadata Terms

DCMI Metadata Terms, by the Dublin Core Metadata Initiative, DCMI Recommendation issued 2020-01-20,
https://www.dublincore.org/specifications/dublin-core/dcmi-terms/. Licensed under the Creative Commons Attribution 4.0
International licence, https://creativecommons.org/licenses/by/4.0/ (https://www.dublincore.org/about/copyright/).
The labels and definitions are DCMI's, unchanged.

## Apache Ossie

Apache Ossie (incubating), formerly Open Semantic Interchange, https://github.com/apache/ossie, licensed under the
Apache License, Version 2.0 (above). Only its name and draft version are named here: it is the format a metrics
sub-graph could be written in, and it supplies no concept.
