# Bitácora de problemas y soluciones

Todo lo que salió mal construyendo esta app, en el orden en que pasó: qué se vio, por qué pasó, cómo se resolvió y qué se aprendió. Los errores de diagnóstico también están, porque enseñan tanto como los errores del sistema.

## Resumen

| # | Problema | Tipo | Estado |
| --- | --- | --- | --- |
| 1 | Sin acceso a modelos de Anthropic en la cuenta | Plataforma | Resuelto: Amazon Nova |
| 2 | `aws-cdk-lib` no instala en Python 3.9 | Entorno | Resuelto: uv con Python 3.12 |
| 3 | Manuales con texto ilegible o enlaces que no son PDF | Datos | Resuelto: corpus verificado |
| 4 | La cuenta bloquea buckets públicos | Seguridad | Resuelto: CloudFront con OAC |
| 5 | Un `cdk deploy` detenido siguió desplegando | Herramientas | Resuelto |
| 6 | Reorganizar el código obligó a destruir la pila | IaC | Resuelto |
| 7 | `aws login` no funciona con boto3 | Entorno | Resuelto: `boto3[crt]` |
| 8 | Las tablas de mantenimiento perdían sus columnas | RAG: parsing | Resuelto: parser con visión |
| 9 | Cambiar el parser no garantizaba reprocesar | RAG: ingesta | Resuelto: fuente de datos nueva |
| 10 | La ingesta tardaba más de 30 minutos | Operación | Explicado: cuota, no costo |
| 11 | Las métricas de tokens marcaban 0 | Costos | Explicado |
| 12 | **El parser inventó una tabla en la página del índice** | RAG: alucinación | Resuelto: prompt sin ejemplos |
| 13 | La Lambda era alcanzable sin pasar por CloudFront | Seguridad | Resuelto: OAC y `AWS_IAM` |
| 14 | La demo quedaba expuesta de noche | Seguridad | Resuelto: interruptor y geo |
| 15 | El aviso de "llévalo al taller" salía de más | Producto | Resuelto: reglas por síntomas |
| 16 | Una versión de guardrail que no se actualiza sola | IaC | Resuelto |
| 17 | **Diagnóstico equivocado:** un dato "inventado" que sí estaba | Proceso | Corregido |
| 18 | **Ninguna alarma llegaba a Slack** | Observabilidad | Resuelto: política de SNS |
| 19 | El set dorado no pasa: 8 de 10 | Evaluación | Abierto |
| 20 | Respuesta respaldada pero incompleta | RAG: generación | Abierto |
| 21 | **Páginas del manual que nunca llegaron al índice** | RAG: ingesta | Abierto: causa por confirmar |
| 22 | Un "no encontré" dicho con otras palabras pasaba como respuesta | Producto | Abierto |

---

## 1. Sin acceso a modelos de Anthropic en la cuenta

- **Síntoma:** el plan inicial usaba Claude como generador, pero la cuenta no tenía acceso a modelos de Anthropic.
- **Causa:** los modelos de terceros requieren habilitación y se cobran por AWS Marketplace; los créditos de AWS pueden no cubrirlos `[verificar]`.
- **Solución:** Amazon Nova 2 Lite como generador y Nova Lite como parser. Son modelos de AWS: leen imágenes, son baratos y los cubren los créditos. Nova 2 Lite se invoca con el perfil de inferencia `us.amazon.nova-2-lite-v1:0`, porque no admite invocación directa en la región.
- **Lección:** el modelo es una pieza reemplazable si el resto del diseño no depende de él.

## 2. `aws-cdk-lib` no instala en Python 3.9

- **Síntoma:** `pip` solo encontraba versiones viejas de `aws-cdk-lib`.
- **Causa:** las versiones recientes exigen Python 3.10 o superior, y el sistema tenía 3.9.
- **Solución:** [uv](https://docs.astral.sh/uv/) con Python 3.12 gestionado por uv, sin tocar el Python del sistema. El proyecto vive en `infra/pyproject.toml` con `uv.lock`.
- **Lección:** fijar la versión del intérprete en el proyecto, no depender de la del equipo.

## 3. Manuales con texto ilegible o enlaces que no son PDF

- **Síntoma:** algunos enlaces de manuales devolvían una página web en vez de un PDF, y los manuales de otra marca se extraían como caracteres sin sentido.
- **Causa:** sitios que responden con la aplicación web para cualquier ruta, y PDF con fuentes sin mapeo de caracteres.
- **Solución:** antes de aceptar un manual, se verifica que la descarga sea `application/pdf` y que el texto extraído contenga palabras esperadas (por ejemplo, "aceite"). Se descartaron los que no pasaron.
- **Lección:** validar la calidad de los datos **antes** de ingerirlos. Basura que entra, basura que sale.

## 4. La cuenta bloquea buckets públicos

- **Síntoma:** el plan de servir la web desde un bucket S3 público no era viable.
- **Causa:** la cuenta tiene activado el bloqueo de acceso público de S3 a nivel de cuenta.
- **Solución:** bucket privado servido por CloudFront con Origin Access Control (OAC). De paso da HTTPS al celular.
- **Lección:** el bloqueo era correcto; el diseño era el que tenía que cambiar.

## 5. Un `cdk deploy` detenido siguió desplegando

- **Síntoma:** después de detener un despliegue, el siguiente `cdk deploy` decía *"Other CLIs are currently reading from cdk.out"*, y la pila apareció en `CREATE_IN_PROGRESS`.
- **Causa:** al detener la terminal, el proceso de Node del CDK quedó vivo y siguió con el despliegue.
- **Solución:** dejar que terminara (era la misma pila) y cerrar el proceso huérfano para liberar el candado de `cdk.out`.
- **Lección:** detener el CLI no cancela CloudFormation. Hay que revisar el estado de la pila, no solo la terminal.

## 6. Reorganizar el código obligó a destruir la pila

- **Síntoma:** partir el stack en un archivo por carril cambiaba los ID lógicos de CloudFormation.
- **Causa:** con ID nuevos, CloudFormation crea los recursos nuevos antes de borrar los viejos, y la Knowledge Base y el guardrail tienen nombre fijo: chocarían.
- **Solución:** `cdk destroy` y `cdk deploy` desde cero, antes de tener datos ingeridos. De paso quedó probado el `destroy`.
- **Lección:** refactorizar IaC no es gratis: los ID lógicos son parte del contrato con los recursos desplegados.

## 7. `aws login` no funciona con boto3

- **Síntoma:** `ingestar.py` fallaba con *"Using the login credential provider requires an additional dependency"*.
- **Causa:** las credenciales de `aws login` necesitan el extra `crt` de botocore. La AWS CLI lo trae; boto3, no.
- **Solución:** `uv add "boto3[crt]"`.
- **Lección:** que la CLI funcione no garantiza que el SDK funcione con las mismas credenciales.

## 8. Las tablas de mantenimiento perdían sus columnas

- **Síntoma:** a "¿cada cuánto cambio el aceite?" el asistente respondía, con razón, que no lo encontraba.
- **Causa:** el parser por defecto solo extrae texto. La fila del cuadro de mantenimiento quedaba como `Aceite de motor / Cambiar / √√√√√√`: los chulos perdían su columna de kilómetros. **Ningún chunking ni embedding puede recuperar lo que el parser ya perdió.**
- **Solución:** parsing con un modelo con visión (Nova Lite), que lee cada página como imagen y transcribe las tablas a Markdown.
- **Costo:** tokens por página, solo al ingerir; menos de un dólar por ingesta completa `[verificar en Cost Explorer]`.
- **Lección:** el parsing es la decisión más subestimada de un RAG.

## 9. Cambiar el parser no garantizaba reprocesar

- **Síntoma:** `cdk diff` mostraba que la fuente de datos se actualizaría en sitio.
- **Causa:** con los mismos PDF, la ingesta podía considerarlos "sin cambios" y saltárselos `[verificar]`. Además, la fuente vieja tenía política `RETAIN`: sus vectores habrían seguido vivos.
- **Solución:** fuente de datos nueva con un ID lógico versionado (`FuenteDatosV2`) y política `DELETE`. Al reemplazarla, la vieja se borra con sus vectores.
- **Lección:** cambiar cómo se procesa el conocimiento obliga a reprocesarlo entero, y conviene forzarlo de forma explícita.

## 10. La ingesta tardaba más de 30 minutos

- **Síntoma:** unas 1.100 páginas tardaron entre 33 y 40 minutos en ingerirse.
- **Causa:** la cuota de llamadas por minuto al modelo parser en una cuenta nueva. La Knowledge Base espera entre llamadas.
- **Solución:** ninguna necesaria. La ingesta corre en AWS: el script solo consulta el estado y se puede cerrar.
- **Lección:** el tiempo no es plata. Se paga por token, no por minuto.

## 11. Las métricas de tokens marcaban 0

- **Síntoma:** las métricas `AWS/Bedrock` de tokens daban 0 para Nova Lite y Titan después de una ingesta completa.
- **Causa:** las llamadas que la Knowledge Base hace por dentro al ingerir no aparecen como invocaciones de la cuenta en esas métricas `[verificar]`.
- **Solución:** medir el costo real en Cost Explorer (con un día de retraso) y cubrir el riesgo con el presupuesto de AWS Budgets.
- **Lección:** saber de dónde sale cada número antes de prometer que se puede medir.

## 12. El parser inventó una tabla en la página del índice

- **Síntoma:** el primer resultado para "¿cada cuánto cambio el aceite?" era la "página 89", con una tabla de cambio de aceite y símbolos `√ ✓ ● X`. En el PDF real, la página 89 es el **índice alfabético**.
- **Causa:** el prompt del parser traía un ejemplo concreto ("Aceite de motor: cambiar a los 500 km, 3000 km…") y una lista de símbolos. Ante una página sin tablas, el modelo **copió el ejemplo como si fuera contenido**.
- **Cómo se detectó:** contrastando la página citada con el PDF fuente.
- **Solución:** prompt sin ejemplos de contenido, con reglas explícitas: no crear tablas que no existen, transcribir el índice tal cual y nunca copiar las instrucciones. Reingesta completa (ver el problema 9).
- **Verificación:** después de la reingesta, la página 89 ya no aparece y ningún fragmento contiene los símbolos del ejemplo.
- **Lección:** las alucinaciones también pasan al **ingerir**, no solo al responder. Y un ejemplo en un prompt puede convertirse en contenido.

## 13. La Lambda era alcanzable sin pasar por CloudFront

- **Síntoma:** la Function URL de la Lambda era pública (`auth NONE`): cualquiera con la URL podía llamarla directo.
- **Causa:** el diseño inicial ponía CloudFront solo delante de la web, no de la API.
- **Solución:** CloudFront como única puerta. `/*` va a S3 y `/api/*` va a la Lambda, las dos con OAC. La Function URL exige firma (`AWS_IAM`) y solo CloudFront firma. Para los `POST`, el navegador envía el SHA-256 del cuerpo en el header `x-amz-content-sha256`, como exige Lambda. Además desaparece CORS, porque todo es el mismo dominio.
- **Lección:** el backend no debe ser alcanzable sin pasar por el borde, que es donde se ponen WAF y los límites.

## 14. La demo quedaba expuesta de noche

- **Síntoma:** la web quedaba pública mientras nadie la vigilaba, y cada pregunta gasta tokens.
- **Solución:** un interruptor en CDK (`--context web_activa=false`) que deshabilita la distribución sin borrar nada, y una restricción geográfica permanente a Colombia.
- **Lección:** apagar cuesta cero. Para una demo, el control más barato es no estar expuesta cuando no se usa.

## 15. El aviso de "llévalo al taller" salía de más

- **Síntoma:** el aviso aparecía incluso en "¿qué cantidad de aceite lleva el motor?".
- **Causa:** la regla buscaba la palabra "aceite" en la pregunta **y en la respuesta del modelo**.
- **Solución:** la regla se activa solo por **síntomas** en la pregunta o en la foto (luces rojas o encendidas, frenos, temperatura, fugas, ruidos, humo), no por lo que responde el modelo. Se probó con 11 casos.
- **Lección:** un aviso que sale siempre deja de servir.

## 16. Una versión de guardrail que no se actualiza sola

- **Síntoma:** al agregar reglas al guardrail, la Lambda habría seguido usando la versión anterior.
- **Causa:** el recurso `AWS::Bedrock::GuardrailVersion` se crea una vez; cambiar el guardrail solo cambia el borrador.
- **Solución:** cambiar la descripción de la versión fuerza a CloudFormation a crear una nueva. Quedó documentado en `guardrail.py`.
- **Lección:** leer el `cdk diff` antes de desplegar: mostró `replace` justo donde tenía que estar.

## 17. Diagnóstico equivocado: un dato "inventado" que sí estaba

- **Síntoma:** el asistente respondió *"el aceite debe cambiarse cada 3000 km (pág. 44)"*, y se concluyó que el "3000 km" era inventado porque el fragmento parecía tener solo los chulos, sin los kilómetros.
- **Causa del error de diagnóstico:** al inspeccionar el fragmento se recortó el texto alrededor de la palabra "aceite", y el encabezado con los kilómetros, que estaba en el mismo fragmento más arriba, quedó fuera de la vista.
- **Qué mostró la medición:** con el grounding contextual activo, esa respuesta sacó 0,92: estaba respaldada. Al leer el fragmento completo apareció el encabezado `500 km | 3000 km (1900 mi) | 6000 km | …`.
- **El problema real:** la respuesta está respaldada pero **incompleta**. El cuadro marca el primer cambio a los 500 km, y la respuesta lo omite.
- **Lección:** antes de concluir, leer la evidencia completa. Y el grounding mide que lo dicho esté en la fuente, **no que esté completo**: la completitud la valida el set dorado con datos esperados.

## 18. Ninguna alarma llegaba a Slack

- **Síntoma:** al forzar una alarma a estado ALARM, su historial decía *"CloudWatch Alarms is not authorized to perform: SNS:Publish"*. A Slack no llegó nada.
- **Causa:** para que AWS Budgets publicara en el tema de SNS se agregó un permiso a la política del tema. Esa política **reemplaza** la política por defecto de SNS, que es la que dejaba publicar a CloudWatch. Quedó solo el permiso de Budgets.
- **Cómo se detectó:** forzando una alarma a mano (`set-alarm-state`) y leyendo el historial de acciones, en vez de asumir que funcionaba.
- **Solución:** la política del tema declara explícitamente a CloudWatch y a Budgets, limitados a la cuenta (`aws:SourceAccount`).
- **Verificación:** con el arreglo desplegado, la misma alarma forzada registra *"Successfully executed action"* hacia el tema de SNS, donde antes decía *"Failed to execute action"*, y el mensaje llega al canal de Slack.
- **Lección:** una alerta que nunca se ha probado no es una alerta. Hay que forzar la falla.

## 19. El set dorado no pasa: 8 de 10

- **Qué se hizo:** 10 preguntas con la respuesta verificada a mano contra el PDF, cubriendo los 6 manuales, los dos idiomas, un caso de seguridad y tres trampas. Ver [`pruebas/`](../pruebas/).
- **Resultado:** 8 de 10. Las tres trampas pasan: vehículo equivocado, fuera del manual e inyección. El umbral pide 9 de 10.
- **Fallos:** los problemas 20 y 21. El 22 apareció al revisar el detalle de las respuestas.
- **Lección:** el set dorado encontró en 30 segundos dos fallos que las pruebas a mano no habían visto. Por eso es la compuerta antes de cualquier cambio.

## 20. Respuesta respaldada pero incompleta

- **Síntoma:** a "¿cada cuánto cambio el aceite?" (Yamaha XTZ125) responde "cada 3000 km". El cuadro de mantenimiento marca el primer cambio a los **500 km** y luego cada 3000.
- **Causa:** el grounding pasa (el dato está en la fuente), pero nada obliga a la respuesta a incluir **todos** los valores relevantes del fragmento.
- **Solución propuesta:** pedir en las instrucciones del generador que incluya el primer intervalo y la frecuencia cuando el fragmento traiga una tabla de intervalos, y volver a correr el set dorado.
- **Lección:** el grounding no mide completitud; los datos esperados del set dorado, sí.

## 21. Páginas del manual que nunca llegaron al índice

- **Síntoma:** a "¿qué aceite le pongo y cada cuánto lo cambio?" (Bajaj Pulsar NS125) no encuentra el dato. La página con la especificación (SAE 20W50, cambio cada 10.000 km) existe en el PDF.
- **Qué se midió:**
  - La misma pregunta **en inglés** tampoco trae esa página, así que el idioma no es la causa.
  - Pidiendo hasta 100 fragmentos de ese manual, aparecen 58 fragmentos que empiezan en 50 páginas distintas. **10 páginas no tienen ningún fragmento**, entre ellas la de la especificación del aceite.
  - Además, la búsqueda devuelve menos fragmentos que los pedidos (3 o 4 de 8) `[verificar causa]`.
- **Causas posibles, por confirmar:** que el parser con visión haya devuelto vacío en páginas con mucha imagen, o que el texto de esas páginas haya quedado dentro de un fragmento que empieza en la página anterior.
- **Pendiente:** revisar la salida del parser por página y, si se confirma, subir el número de fragmentos recuperados y validar en la ingesta que cada página produzca texto.
- **Lección:** que una ingesta diga "6 de 6 documentos indexados" no significa que **todas las páginas** estén en el índice. Hay que medir cobertura por página.

## 22. Un "no encontré" dicho con otras palabras pasaba como respuesta

- **Síntoma:** en el caso de la Pulsar, el modelo respondió *"No encontré información específica sobre qué tipo de aceite usar…"*, sin citas, y la app lo entregó como respuesta normal.
- **Causa:** el código detecta el "no encontrado" buscando la frase exacta de las instrucciones. El modelo la dijo con otras palabras.
- **Solución propuesta:** tratar como "no encontrado" toda respuesta que no cite ninguna página. Es una regla en código y no depende de la redacción del modelo.
- **Lección:** no confiar en que el modelo repita una frase exacta. Las reglas deben verificar una señal estructural (hay o no hay citas).

---

**Nota sobre los números de página `[verificar]`:** cada fragmento se etiqueta con la página donde **empieza**. Si empieza al final de una página, la cita puede quedar una página antes de donde está el dato (en un caso, la cita dijo 50 y el dato está en la 51 del visor de PDF). Por eso el set dorado acepta una página de diferencia.
