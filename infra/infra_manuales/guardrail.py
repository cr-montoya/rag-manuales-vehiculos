"""Carril 4. Guardrail: filtro de entrada y salida, fuera del modelo.

Lo que el guardrail bloquea no depende de que el modelo "se porte bien".
"""

from aws_cdk import aws_bedrock as bedrock
from constructs import Construct


class Guardrail(Construct):
    def __init__(self, scope: Construct, construct_id: str) -> None:
        super().__init__(scope, construct_id)

        self.guardrail = bedrock.CfnGuardrail(
            self,
            "Guardrail",
            name="manuales-vehiculos",
            description="Bloquea prompt injection y enmascara datos personales",
            blocked_input_messaging="No puedo procesar esa solicitud. Pregúntame sobre el manual de tu vehículo.",
            blocked_outputs_messaging="No puedo responder eso. Pregúntame sobre el manual de tu vehículo.",
            content_policy_config=bedrock.CfnGuardrail.ContentPolicyConfigProperty(
                filters_config=[
                    # PROMPT_ATTACK detecta intentos de cambiar las instrucciones
                    # ("ignora lo anterior y..."). Solo aplica a la entrada.
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="PROMPT_ATTACK", input_strength="HIGH", output_strength="NONE"
                    ),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="INSULTS", input_strength="MEDIUM", output_strength="MEDIUM"
                    ),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="VIOLENCE", input_strength="MEDIUM", output_strength="MEDIUM"
                    ),
                ]
            ),
            # Si alguien escribe su correo o teléfono, no llega en claro al modelo ni a los logs
            sensitive_information_policy_config=bedrock.CfnGuardrail.SensitiveInformationPolicyConfigProperty(
                pii_entities_config=[
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type=t, action="ANONYMIZE")
                    for t in ["EMAIL", "PHONE", "NAME"]
                ]
            ),
        )
        # Versión fija del guardrail: la Lambda usa una versión probada, no el borrador
        self.version = bedrock.CfnGuardrailVersion(
            self, "Version", guardrail_identifier=self.guardrail.attr_guardrail_id
        )
