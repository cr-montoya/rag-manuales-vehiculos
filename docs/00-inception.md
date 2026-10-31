# Inception Fase 0: asistente de manuales de vehículos

AI-DLC: la IA propone y pregunta, el humano decide. Se valida antes de escribir código. Alcance: uno o dos bloques de 90 minutos.

## 1. Intención

Que una persona no técnica pregunte por su moto o su carro ("¿cada cuánto cambio el aceite?", o una foto del tablero: "¿qué significa esta luz?") y reciba lo que dice el manual de **ese** vehículo, con la página citada. Si el tema es de seguridad (frenos, temperatura, aceite), la respuesta termina con "llévalo al taller".

## 2. Criterios (en orden de peso)

1. **No responder con el manual equivocado.** Es el riesgo principal.
2. **Respuesta trazable:** siempre con cita de documento y página, o "no está en el manual".
3. **Usable desde el celular** por alguien no técnico.
4. **Costo cercano a cero y destruible** con `cdk destroy`.

## 3. Escalón de autonomía

**Escalón 2: RAG**, con una llamada multimodal para leer la foto. No hace falta agente: no hay acciones ni un camino que dependa del caso. Alternativa descartada: un agente con herramientas (escalón 4). Ese escalón suma latencia y riesgo sin aportar valor aquí.

## 4. Diseño propuesto (la decisión es del humano)

| Pieza | Propuesta | Alternativa descartada y por qué |
| --- | --- | --- |
| Fuente | S3 con un `.metadata.json` por PDF: `tipo`, `marca`, `modelo`, `anio` | Un índice por vehículo: no escala y duplica infraestructura |
| RAG | Bedrock Knowledge Base | Pipeline propio con LangChain: más código y operación |
| Vector store | **S3 Vectors** (GA desde diciembre de 2025, integrado con Knowledge Bases) | OpenSearch Serverless: tiene un piso de unos USD 170 al mes `[verificar]`, injustificado para 3 PDF |
| Embeddings | Titan Text Embeddings V2 | Cohere: no aporta para 3 documentos |
| Generador | **Claude Haiku 5.5** (visión, rapidez, costo); escalar a Sonnet 5.5 solo si el set dorado muestra que lee mal las fotos | Sonnet de entrada: más caro sin evidencia de que haga falta |
| Seguridad | Bedrock Guardrails básico: temas fuera de alcance y prompt injection | Sin guardrail: alguien no técnico lo usará con preguntas libres |
| Interfaz | Página web mínima (S3 y CloudFront) con una Lambda detrás: selector de vehículo, texto y foto | Streamlit en un servidor: hay que operarlo y no es serverless |
| Acceso | Enlace compartido con límite de uso (throttling) y una alarma de presupuesto | Cognito: excesivo para una demo de un día |

**Flujo:** el usuario elige su vehículo, o lo dice en el texto. Sin vehículo identificado, el sistema pregunta antes de responder. Con vehículo, la búsqueda se filtra por `marca`, `modelo` y `anio` antes de comparar vectores. Si hay foto, Haiku la describe primero ("testigo de presión de aceite") y esa descripción se usa como consulta. La respuesta lleva la cita y, si el tema es de seguridad, la advertencia del taller.

## 5. Supuestos

| Supuesto | Si no fuera así, cambia |
| --- | --- |
| Cuenta personal con créditos de AWS en us-east-1 | La región y los IDs de modelo |
| Los PDF tienen texto extraíble, no son escaneos | Habría que agregar parsing con un modelo fundacional o Textract en la ingesta |
| La cita de página sale de los metadatos de ubicación que devuelve la Knowledge Base `[verificar en la prueba]` | Se divide cada PDF por página antes de ingerirlo |
| La regla "llévalo al taller" vive en el prompt, más una lista de palabras clave en la Lambda | Se mueve a una regla determinista en código, fuera del modelo (así quedó implementado) |

## 6. Criterios de aceptación: set dorado de 10 preguntas

- **6 preguntas de texto con respuesta en el manual**, como intervalo de aceite, presión de llantas o capacidad del tanque. Pasan si la cita apunta a la página correcta.
- **2 fotos del tablero.** Pasan si identifican el testigo y responden con cita y con la advertencia de seguridad.
- **Trampa 1, vehículo equivocado:** se pregunta por la moto con el carro seleccionado. Pasa si no responde con el otro manual.
- **Trampa 2, fuera de los manuales:** por ejemplo, "¿cuánto cuesta el seguro?". Pasa si responde "no está en el manual".

Umbral propuesto: 9 de 10, con las 2 trampas obligatorias `[decidir]`.

## 7. Preguntas abiertas para el humano

1. **Corpus:** ¿manuales reales o ficticios? Los manuales oficiales de los fabricantes tienen derechos de autor, y no encontré una licencia que permita usarlos en una demo `[verificar en el sitio de cada fabricante]`. Los ficticios se pueden mostrar y versionar sin ese riesgo.
2. ¿Umbral de 9 de 10?
3. ¿Haiku 5.5 como generador inicial?
