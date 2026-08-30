ALTER TABLE "generation_tasks" ADD COLUMN "message" text;
ALTER TABLE "generation_tasks" ADD COLUMN "error_code" varchar(64);
ALTER TABLE "generation_tasks" ADD COLUMN "selected_action" varchar(32);
ALTER TABLE "generation_tasks" ADD COLUMN "artifacts" text;
ALTER TABLE "generation_tasks" ADD COLUMN "display_code" varchar(6);
ALTER TABLE "generation_tasks" ADD COLUMN "delivery_status" varchar(32);
