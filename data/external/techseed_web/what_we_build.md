# TechSeed areas of expertise (techseed.se)

Source: <https://techseed.se/#what-we-build>, section "What we do",
carousel on the right. Fetched 2026-10-29.

Text is verbatim from the page, typos included ("Entreprise" in area 4).
All of it is in English.

Extraction: the carousel is a Webflow CMS list (`.vertical-list`, Slick
vertical slider) with 6 `.vertical-item` elements in the static HTML. The
browser DOM matched: 15 slides, minus 9 `slick-cloned` loop clones, leaves 6
real slides and 6 nav dots. Autoplay cycled back after 6. No API call loads the
slides. The page also runs an Intellimize personalisation script, which in
theory could show other visitors different content.

## 1. Mechatronics + Physical systems

Modern products depend on tight integration between mechanics, electronics,
sensors, and software. We help organizations design and integrate physical
systems that perform reliably in demanding real-world environments.

- Mechanical engineering
- Electronics design
- PCB design
- Sensors & actuators

## 2. Embedded Systems

We develop embedded systems, FPGA, and hardware solutions that combine
innovation with long-term operational stability. As products become smarter and
more connected, software reliability, security, and real-time performance
become business-critical. We develop embedded systems that balance innovation
with long-term operational stability.

- Embedded Linux
- Real-time systems
- FPGA
- Verification/Test
- Specification

## 3. Connected Systems + IoT

When machines, facilities, and assets operate in isolation, opportunities for
optimization are often missed. We connect physical systems and operational
environments to provide real-time visibility, monitoring, and actionable
insight.

- IoT platforms
- Edge computing
- 5G
- BLE
- MQTT

## 4. Platforms + Cloud + Entreprise Integration

Over time, most organizations accumulate platforms, integrations, and data silos
that make change difficult and costly. We help create scalable foundations where
systems, services, and data work together reliably.

- Cloud (Azure / AWS / GCP)
- APIs
- Integration
- Enterprise architecture

## 5. Data + AI + Operational Intelligence

Many organizations collect vast amounts of operational data but struggle to turn
it into decisions, predictions, and measurable business value. We help connect,
structure, and apply data to improve forecasting, automation, and operational
performance.

- Data engineering
- Machine learning
- Analytics
- Computer Vision
- Prediction

## 6. Human Interaction + XR + Operational Interfaces

As operational environments become more complex, people need better ways to
understand, navigate and interact with information. We help organizations
combine spatial computing, XR, digital twins and operational data to improve
training, collaboration, situational awareness and decision-making.

- XR (AR/VR/MR)
- Spatial Computing
- Digital Twins
- Human-Machine Interaction
- Operational Visualization

## Tag list in the same section

The same section also shows a tag list (`.vertical-tags`) naming the same 6
areas. The page displays it in uppercase, but the underlying text differs from
the carousel titles in places:

| Carousel title | Tag list text |
| --- | --- |
| Mechatronics + Physical systems | Mechatronics + Physical systems |
| Embedded Systems | Embedded systems |
| Connected Systems + IoT | connected systems + iot |
| Platforms + Cloud + Entreprise Integration | platforms + cloud + enterprise integration |
| Data + AI + Operational Intelligence | Data + AI + Operational Intelligence |
| Human Interaction + XR + Operational Interfaces | Human Interaction + XR + Operational Interface |

## Positioning copy in the section (not expertise areas)

- Heading: "Smart Things. / Smart Industries. / Smart Societies."
- "We bring intelligence to the things that matter, the real stuff. Whether
  it's something you use, monitor, or experience. Put simply, we make the
  physical world a little smarter."
- Heading: "Our stack is our DNA, and the backbone in everything we do."
- "We connect physical systems, software, data and people into operational
  systems that work in the real world. Most consultancies specialize in one
  layer. We connect them."
