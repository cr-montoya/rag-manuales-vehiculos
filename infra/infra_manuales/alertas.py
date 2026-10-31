"""Carril 7. Alertas: CloudWatch Alarms y AWS Budgets, avisando en Slack.

Ruta del aviso: alarma o presupuesto -> Amazon SNS -> Amazon Q Developer in chat
applications (antes AWS Chatbot) -> canal de Slack. Sin webhook, sin secreto que
guardar y sin Lambda propia: el mejor secreto es el que no existe.

Criterio: cada alerta tiene una acción clara cuando suena. Las 4 y 5 son
propias de un sistema de IA: un RAG puede estar arriba y responder mal.

| # | Alerta              | Acción si suena                                        |
| 1 | Errores de Lambda   | Revisar en los logs qué paso falló                     |
| 2 | Lentitud (p90)      | Ver si es Bedrock o la búsqueda                        |
| 3 | Abuso de la web     | Poner AWS WAF con límite de tasa en CloudFront         |
| 4 | Ataques bloqueados  | Revisar el origen; es evidencia de que el control sirve |
| 5 | Calidad del RAG     | Correr el set dorado: posible regresión de parsing     |
| 6 | Costo mensual       | Destruir la pila o investigar el gasto                 |

Los IDs de Slack NO van en el repo: se pasan al desplegar con
--context slack_workspace=T... --context slack_canal=C...
Antes hay que autorizar el workspace una vez en la consola (ver README).
"""

from aws_cdk import (
    Annotations,
    Duration,
    aws_budgets as budgets,
    aws_chatbot as chatbot,
    aws_cloudwatch as cw,
    aws_cloudwatch_actions as cw_actions,
    aws_iam as iam,
    aws_logs as logs,
    aws_sns as sns,
)
from constructs import Construct

from .asistente import Asistente

ESPACIO_METRICAS = "ManualesVehiculos"  # namespace propio para las métricas que salen de los logs


class Alertas(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        asistente: Asistente,
        slack_workspace: str | None,
        slack_canal: str | None,
    ) -> None:
        super().__init__(scope, construct_id)

        # Un solo tema para todas las alertas: las alarmas y el presupuesto publican aquí
        self.tema = sns.Topic(self, "Tema", display_name="Alertas manuales de vehículos")

        if slack_workspace and slack_canal:
            # El tema se conecta al canal de Slack. Quien esté en el canal solo puede
            # LEER métricas desde Slack (guardrail de solo lectura), no cambiar nada.
            chatbot.SlackChannelConfiguration(
                self,
                "Slack",
                slack_channel_configuration_name="alertas-manuales-vehiculos",
                slack_workspace_id=slack_workspace,
                slack_channel_id=slack_canal,
                notification_topics=[self.tema],
                guardrail_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("CloudWatchReadOnlyAccess")],
                logging_level=chatbot.LoggingLevel.ERROR,
            )
        else:
            Annotations.of(self).add_warning(
                "Sin --context slack_workspace=... y slack_canal=...: las alarmas existen pero nadie recibe el aviso."
            )
        accion = cw_actions.SnsAction(self.tema)
        funcion = asistente.funcion

        def alarma(id_: str, descripcion: str, metrica: cw.IMetric, umbral: float,
                   comparacion=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD) -> None:
            a = cw.Alarm(
                self,
                id_,
                alarm_description=descripcion,
                metric=metrica,
                threshold=umbral,
                evaluation_periods=1,
                comparison_operator=comparacion,
                # Sin tráfico no hay datos: eso no es una falla
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            )
            a.add_alarm_action(accion)

        # 1. Errores técnicos: cualquier excepción no controlada en la Lambda
        alarma("Errores", "La Lambda falló al menos una vez en 5 minutos",
               funcion.metric_errors(period=Duration.minutes(5), statistic="Sum"), 1)

        # 2. Lentitud: el 90 % de las respuestas debería tardar menos de 20 s
        alarma("Lentitud", "p90 de duración mayor a 20 s en 15 minutos",
               funcion.metric_duration(period=Duration.minutes(15), statistic="p90"), 20_000,
               cw.ComparisonOperator.GREATER_THAN_THRESHOLD)

        # 3. Abuso: la web es pública en CloudFront; muchas llamadas = alguien la está usando de más
        alarma("Abuso", "Más de 200 invocaciones en una hora",
               funcion.metric_invocations(period=Duration.hours(1), statistic="Sum"), 200,
               cw.ComparisonOperator.GREATER_THAN_THRESHOLD)

        # Métricas que salen de los logs JSON de handler.py (filtros de métrica)
        def metrica_de_logs(id_: str, nombre: str, patron: str) -> cw.Metric:
            logs.MetricFilter(
                self,
                id_,
                log_group=asistente.logs,
                metric_namespace=ESPACIO_METRICAS,
                metric_name=nombre,
                filter_pattern=logs.FilterPattern.literal(patron),
                metric_value="1",
            )
            return cw.Metric(namespace=ESPACIO_METRICAS, metric_name=nombre, statistic="Sum")

        bloqueos = metrica_de_logs("FiltroBloqueos", "GuardrailBloqueos", '{ $.evento = "guardrail_bloqueo" }')
        respuestas = metrica_de_logs("FiltroRespuestas", "Respuestas", '{ $.evento = "respuesta" }')
        no_encontradas = metrica_de_logs(
            "FiltroNoEncontradas", "NoEncontradas", '{ ($.evento = "respuesta") && ($.encontrado IS FALSE) }'
        )

        # 4. Ataques: varios bloqueos del guardrail seguidos
        alarma("Ataques", "5 o más bloqueos del guardrail en 15 minutos",
               bloqueos.with_(period=Duration.minutes(15)), 5)

        # 5. Calidad: porcentaje de "No encontré eso en el manual".
        # Solo cuenta con al menos 5 respuestas en la hora, para no alarmar por una sola pregunta.
        tasa = cw.MathExpression(
            expression="IF(resp >= 5, 100 * FILL(noenc, 0) / resp, 0)",
            using_metrics={"resp": respuestas, "noenc": no_encontradas},
            period=Duration.hours(1),
            label="% de respuestas sin encontrar",
        )
        alarma("Calidad", "Más del 40 % de respuestas 'no encontrado' en una hora", tasa, 40,
               cw.ComparisonOperator.GREATER_THAN_THRESHOLD)

        # 6. Costo: presupuesto mensual de la cuenta, avisando por el mismo tema SNS.
        # AWS Budgets necesita permiso para publicar en el tema.
        self.tema.add_to_resource_policy(
            iam.PolicyStatement(
                actions=["sns:Publish"],
                principals=[iam.ServicePrincipal("budgets.amazonaws.com")],
                resources=[self.tema.topic_arn],
            )
        )
        aviso = lambda tipo, porcentaje: budgets.CfnBudget.NotificationWithSubscribersProperty(
            notification=budgets.CfnBudget.NotificationProperty(
                notification_type=tipo,
                comparison_operator="GREATER_THAN",
                threshold=porcentaje,
                threshold_type="PERCENTAGE",
            ),
            subscribers=[budgets.CfnBudget.SubscriberProperty(subscription_type="SNS", address=self.tema.topic_arn)],
        )
        budgets.CfnBudget(
            self,
            "Presupuesto",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name="manuales-vehiculos-mensual",
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(amount=10, unit="USD"),
            ),
            notifications_with_subscribers=[
                aviso("ACTUAL", 80),  # ya se gastó el 80 %
                aviso("FORECASTED", 100),  # al ritmo actual, se pasará del 100 %
            ],
        )
