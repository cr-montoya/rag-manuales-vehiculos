"""Carril 6. Web: CloudFront como única puerta de entrada.

Un solo dominio HTTPS con dos rutas:
- /*      -> bucket S3 privado con la SPA (Origin Access Control)
- /api/*  -> Function URL de la Lambda (Origin Access Control)

Ni el bucket ni la Lambda son públicos: solo CloudFront puede llamarlos, firmando
cada petición. Beneficios: no hay CORS (mismo dominio), nadie se salta CloudFront
llamando la Lambda directo, y hay un solo punto para poner AWS WAF si hace falta.
"""

from pathlib import Path

from aws_cdk import (
    RemovalPolicy,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_lambda as lambda_,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
)
from constructs import Construct

from .asistente import Asistente

RUTA_API = "/api/preguntar"


class Web(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        carpeta_web: Path,
        catalogo: list[dict],
        asistente: Asistente,
    ) -> None:
        super().__init__(scope, construct_id)

        bucket = s3.Bucket(
            self,
            "Bucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # Function URL con AWS_IAM: sin una firma válida responde 403.
        # Quien firma es CloudFront (OAC), no el navegador.
        url_funcion = asistente.funcion.add_function_url(auth_type=lambda_.FunctionUrlAuthType.AWS_IAM)

        # Interruptor: con --context web_activa=false la distribución queda deshabilitada
        # (no responde a nadie) sin borrar nada. Útil para apagar la demo de noche.
        activa = self.node.try_get_context("web_activa") != "false"

        distribucion = cloudfront.Distribution(
            self,
            "Distribucion",
            enabled=activa,
            # Solo se atienden peticiones desde Colombia: corta casi todo el tráfico
            # automatizado de afuera, sin costo adicional.
            geo_restriction=cloudfront.GeoRestriction.allowlist("CO"),
            default_root_object="index.html",
            # Ruta por defecto: la SPA desde S3
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            ),
            # Ruta /api/*: la Lambda, sin caché (cada pregunta es distinta)
            additional_behaviors={
                "/api/*": cloudfront.BehaviorOptions(
                    origin=origins.FunctionUrlOrigin.with_origin_access_control(url_funcion),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    # Reenvía los headers del navegador (incluido x-amz-content-sha256) menos Host
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                )
            },
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,  # solo bordes baratos
        )
        # with_origin_access_control también crea los permisos de la Lambda
        # (InvokeFunctionUrl e InvokeFunction) limitados a ESTA distribución.
        self.url = f"https://{distribucion.distribution_domain_name}"

        # Sube la SPA y un config.json con la ruta de la API y el catálogo de vehículos
        s3deploy.BucketDeployment(
            self,
            "SubirWeb",
            sources=[
                s3deploy.Source.asset(str(carpeta_web)),
                s3deploy.Source.json_data(
                    "config.json",
                    {
                        "api": RUTA_API,
                        "vehiculos": [{"id": m["id"], "etiqueta": m["etiqueta"]} for m in catalogo],
                    },
                ),
            ],
            destination_bucket=bucket,
            distribution=distribucion,  # invalida la caché al redesplegar
        )
