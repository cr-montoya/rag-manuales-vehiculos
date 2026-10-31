"""Carril 3. Knowledge Base: el RAG gestionado (parte los PDF, crea embeddings, busca).

Une el carril 1 (de dónde lee) con el carril 2 (dónde escribe los vectores).
"""

from aws_cdk import Stack, aws_bedrock as bedrock, aws_iam as iam
from constructs import Construct

from .fuente import Fuente
from .vectores import Vectores

# Modelo que "lee" cada página del PDF como imagen al ingerir. Nova Lite v1 admite
# invocación directa (on-demand) y es el más barato de los Nova con visión.
MODELO_PARSER = "amazon.nova-lite-v1:0"

PROMPT_PARSER = """Transcribe esta página de un manual del propietario de un vehículo a texto en Markdown.
Reglas:
- Conserva todo el texto, los títulos y las advertencias, en el idioma original.
- Las tablas van como tablas Markdown, con sus encabezados.
- En los cuadros de mantenimiento, cuando una celda tenga un chulo o una marca (√, ✓, ●, X),
  escribe en la fila el valor del encabezado de esa columna. Ejemplo:
  "Aceite de motor: cambiar a los 500 km, 3000 km, 6000 km, 9000 km".
- Para luces y testigos del tablero, describe el símbolo (forma y color) junto a su nombre y significado.
- No agregues información que no esté en la página."""


class Conocimiento(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        fuente: Fuente,
        vectores: Vectores,
        modelo_embeddings: str,
        dimensiones: int,
    ) -> None:
        super().__init__(scope, construct_id)
        pila = Stack.of(self)
        arn_embeddings = f"arn:aws:bedrock:{pila.region}::foundation-model/{modelo_embeddings}"
        arn_parser = f"arn:aws:bedrock:{pila.region}::foundation-model/{MODELO_PARSER}"

        # Rol que asume Bedrock para leer los PDF, generar embeddings y escribir vectores.
        # Mínimo privilegio: solo este bucket, este índice y este modelo.
        rol = iam.Role(
            self,
            "Rol",
            assumed_by=iam.ServicePrincipal(
                "bedrock.amazonaws.com",
                conditions={"StringEquals": {"aws:SourceAccount": pila.account}},
            ),
        )
        rol.add_to_policy(
            iam.PolicyStatement(actions=["bedrock:InvokeModel"], resources=[arn_embeddings, arn_parser])
        )
        fuente.bucket.grant_read(rol)
        rol.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "s3vectors:PutVectors",
                    "s3vectors:GetVectors",
                    "s3vectors:DeleteVectors",
                    "s3vectors:QueryVectors",
                    "s3vectors:GetIndex",
                ],
                resources=[vectores.indice.attr_index_arn],
            )
        )

        self.kb = bedrock.CfnKnowledgeBase(
            self,
            "KnowledgeBase",
            name="manuales-vehiculos",
            role_arn=rol.role_arn,
            knowledge_base_configuration=bedrock.CfnKnowledgeBase.KnowledgeBaseConfigurationProperty(
                type="VECTOR",
                vector_knowledge_base_configuration=bedrock.CfnKnowledgeBase.VectorKnowledgeBaseConfigurationProperty(
                    embedding_model_arn=arn_embeddings,
                    embedding_model_configuration=bedrock.CfnKnowledgeBase.EmbeddingModelConfigurationProperty(
                        bedrock_embedding_model_configuration=bedrock.CfnKnowledgeBase.BedrockEmbeddingModelConfigurationProperty(
                            dimensions=dimensiones, embedding_data_type="FLOAT32"
                        )
                    ),
                ),
            ),
            storage_configuration=bedrock.CfnKnowledgeBase.StorageConfigurationProperty(
                type="S3_VECTORS",
                s3_vectors_configuration=bedrock.CfnKnowledgeBase.S3VectorsConfigurationProperty(
                    vector_bucket_arn=vectores.bucket.attr_vector_bucket_arn,
                    index_arn=vectores.indice.attr_index_arn,
                ),
            ),
        )
        # La KB valida sus permisos al crearse: el rol y su política deben existir antes
        self.kb.node.add_dependency(rol)

        # Fuente de datos: qué carpeta del bucket se ingiere, cómo se lee y cómo se parte.
        #
        # Parsing con modelo fundacional (no el parser por defecto). Lección aprendida:
        # los cuadros de mantenimiento marcan los kilómetros con chulos en columnas;
        # el parser de texto pierde las columnas y "cada cuánto cambio el aceite"
        # no se encontraba. Un modelo con visión lee la página como imagen y
        # transcribe la tabla. Costo: tokens de Nova Lite por página, solo al ingerir.
        #
        # Fragmentos de 300 tokens con 15 % de traslape: cortos para que la cita de
        # página sea precisa, con traslape para no cortar una instrucción a la mitad.
        self.fuente_datos = bedrock.CfnDataSource(
            self,
            "FuenteDatos",
            knowledge_base_id=self.kb.attr_knowledge_base_id,
            name="manuales-pdf-parseo-fm",
            data_deletion_policy="DELETE",  # al borrar la fuente, borra sus vectores
            data_source_configuration=bedrock.CfnDataSource.DataSourceConfigurationProperty(
                type="S3",
                s3_configuration=bedrock.CfnDataSource.S3DataSourceConfigurationProperty(
                    bucket_arn=fuente.bucket.bucket_arn,
                    inclusion_prefixes=[Fuente.PREFIJO],
                ),
            ),
            vector_ingestion_configuration=bedrock.CfnDataSource.VectorIngestionConfigurationProperty(
                parsing_configuration=bedrock.CfnDataSource.ParsingConfigurationProperty(
                    parsing_strategy="BEDROCK_FOUNDATION_MODEL",
                    bedrock_foundation_model_configuration=bedrock.CfnDataSource.BedrockFoundationModelConfigurationProperty(
                        model_arn=arn_parser,
                        parsing_prompt=bedrock.CfnDataSource.ParsingPromptProperty(parsing_prompt_text=PROMPT_PARSER),
                    ),
                ),
                chunking_configuration=bedrock.CfnDataSource.ChunkingConfigurationProperty(
                    chunking_strategy="FIXED_SIZE",
                    fixed_size_chunking_configuration=bedrock.CfnDataSource.FixedSizeChunkingConfigurationProperty(
                        max_tokens=300, overlap_percentage=15
                    ),
                )
            ),
        )
