# 在 AWS 上构建和启动 Listing Studio 1.3.2 AMI

`aws-ami/` 包含 Packer 构建配置、安装脚本和 EC2 启动模板。Packer 会以 Ubuntu Server 24.04 为基础，在你的 AWS 账号及所选区域创建一份私有 Amazon Machine Image。构建过程会启动临时 EC2、安装工作台、检查 API 和首页，再生成 AMI。 [Packer 的 Amazon EBS 构建器](https://developer.hashicorp.com/packer/integrations/hashicorp/amazon/latest/components/builder/ebs) · [AWS AMI 文档](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/AMIs.html)

## 准备

- 在电脑上安装 Packer、AWS CLI v2 和 Session Manager plugin。AWS CLI 使用有权创建 EC2 镜像、实例和 EBS 快照的账号配置。启动模板还会创建一份用于 Systems Manager 的 IAM 实例角色。 [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) · [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)
- 选定区域和一个可访问互联网的公网 IPv4 子网。临时构建机需要下载 Python 依赖，Packer 的临时 SSH 入口只允许构建端的公网 IP。运行机器通过 Session Manager 访问工作台。
- AWS 会按临时实例、运行实例和保存的 EBS 快照收费。发布包附带 `workbench-runtime.tar.gz`。从源码开始时，先按 [README](README.md#从源码重建界面) 构建页面，再运行 `python aws-ami/build_runtime.py`。

## 构建 AMI

在解压后的 `listing-workbench` 目录打开终端。下例的区域和子网换成自己账号里的值。使用指定 AWS CLI 配置时，先按对应配置登录。

```cmd
packer init aws-ami
packer validate -syntax-only aws-ami
packer build -var "aws_region=us-east-1" -var "build_subnet_id=subnet-REPLACE" aws-ami
```

需要搭载 Chromium 且已取得页面采集许可时，在构建命令加上 `-var "install_playwright=true"`。默认镜像可用手动导入、历史归档和授权商品 API。构建完成后，从输出或 `aws-ami/manifest.json` 读取同一区域的 `ami-...`。

`pubtools-ami` 用于发布已构建的 AWS 镜像。此流程在账号内完成构建和私有使用。 [pubtools-ami](https://github.com/release-engineering/pubtools-ami)

## 从 AMI 启动

使用刚构建的 AMI、与子网对应的 VPC 和同一区域。以下为 Windows 命令提示符写法。

```cmd
aws cloudformation deploy ^
  --region us-east-1 ^
  --stack-name listing-workbench-v1 ^
  --template-file aws-ami/launch-workbench.yaml ^
  --capabilities CAPABILITY_IAM ^
  --parameter-overrides WorkbenchAmi=ami-REPLACE VpcId=vpc-REPLACE SubnetId=subnet-REPLACE
```

模板默认以 `t3.medium` 启动，EC2 没有入站规则。实例通过 HTTPS 连接 Systems Manager。子网已配置 NAT 或对应 VPC endpoints 时，可加 `AssignPublicIp=false`。查看堆栈输出里的 `InstanceId`，或在 EC2 控制台复制实例 ID。

```cmd
aws ssm start-session --region us-east-1 --target i-REPLACE --document-name AWS-StartPortForwardingSession --parameters "portNumber=8000,localPortNumber=8080"
```

保持会话窗口开启，从本机浏览器打开 [http://127.0.0.1:8080](http://127.0.0.1:8080)。连接检查可打开 [http://127.0.0.1:8080/api/config](http://127.0.0.1:8080/api/config)。若实例尚未在线，先在 Systems Manager 核对是否已注册为托管节点。 [AWS 端口转发说明](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-sessions-start.html)

## 配置和排查

通过 Session Manager 打开实例的终端会话后，可用以下命令查看工作台。

```bash
sudo systemctl status listing-workbench
sudo journalctl -u listing-workbench -n 50 --no-pager
curl -fsS http://127.0.0.1:8000/api/config
```

需要授权的 Nexscope 商品接口时，运行 `sudoedit /etc/listing-workbench/workbench.env`，填写自己的 `LISTING_NEXSCOPE_API_KEY`，再运行 `sudo systemctl restart listing-workbench`。LLM 接口也在此配置。AMI 构建阶段只包含程序和默认配置。按需配置第三方密钥，生成新的 AMI 前应使用干净的构建流程。

V1.3.2 的构建脚本会安装 Tesseract 和英语语言包。其他语言按需在实例上安装对应语言包并设置 `LISTING_OCR_LANG`。如选 Ollama 本地模型，模型服务需运行在同一台实例。

AMI 用于启动运行工作台的 EC2。工作台的商品归档和项目原文保存在 `/var/lib/listing-workbench/workbench.sqlite3`。升级或更换实例时，先停止旧服务并备份数据库，再恢复到新实例。点击 **获取商品资料（优先归档）** 即可复用同站点、同 ASIN 的原文。主动在线刷新时仍由 Amazon 判定访问条件。
