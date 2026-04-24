// SPDX-License-Identifier: GPL-2.0
#include <linux/gpio/consumer.h>
#include <linux/gpio/driver.h>
#include <linux/gpio/machine.h>
#include <linux/kobject.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/slab.h>

#define PHANDLE_CAM2_EP		0xf101
#define PHANDLE_CAMSS_PORT2_EP	0xf102
#define PHANDLE_CAM3_EP		0xf103
#define PHANDLE_CAMSS_PORT3_EP	0xf104
#define PHANDLE_CAMCC		0x013b
#define CAM_CC_MCLK2_CLK	96
#define CAM_CC_MCLK3_CLK	98
#define CAM_PWDN_GPIO		77

static struct of_changeset ocs;
static bool applied;
static const struct kobj_type *dn_ktype;
static struct gpio_desc *cam_pwdn_desc;

static int set_prop_string(struct device_node *np, const char *name,
			   const char *val)
{
	struct property *prop;

	prop = kzalloc(sizeof(*prop), GFP_KERNEL);
	if (!prop)
		return -ENOMEM;
	prop->name = kstrdup(name, GFP_KERNEL);
	prop->value = kstrdup(val, GFP_KERNEL);
	prop->length = strlen(val) + 1;
	return of_changeset_update_property(&ocs, np, prop);
}

static int add_prop_string(struct device_node *np, const char *name,
			   const char *val)
{
	struct property *prop;

	prop = kzalloc(sizeof(*prop), GFP_KERNEL);
	if (!prop)
		return -ENOMEM;
	prop->name = kstrdup(name, GFP_KERNEL);
	prop->value = kstrdup(val, GFP_KERNEL);
	prop->length = strlen(val) + 1;
	return of_changeset_add_property(&ocs, np, prop);
}

static int add_prop_u32(struct device_node *np, const char *name, u32 val)
{
	struct property *prop;
	__be32 *p;

	prop = kzalloc(sizeof(*prop), GFP_KERNEL);
	if (!prop)
		return -ENOMEM;
	prop->name = kstrdup(name, GFP_KERNEL);
	p = kzalloc(sizeof(*p), GFP_KERNEL);
	if (!p) { kfree(prop); return -ENOMEM; }
	*p = cpu_to_be32(val);
	prop->value = p;
	prop->length = sizeof(*p);
	return of_changeset_add_property(&ocs, np, prop);
}

static int add_prop_u32_array(struct device_node *np, const char *name,
			      const u32 *vals, int count)
{
	struct property *prop;
	__be32 *p;
	int i;

	prop = kzalloc(sizeof(*prop), GFP_KERNEL);
	if (!prop)
		return -ENOMEM;
	prop->name = kstrdup(name, GFP_KERNEL);
	p = kcalloc(count, sizeof(*p), GFP_KERNEL);
	if (!p) { kfree(prop); return -ENOMEM; }
	for (i = 0; i < count; i++)
		p[i] = cpu_to_be32(vals[i]);
	prop->value = p;
	prop->length = count * sizeof(*p);
	return of_changeset_add_property(&ocs, np, prop);
}

static int add_prop_u64(struct device_node *np, const char *name, u64 val)
{
	struct property *prop;
	__be64 *p;

	prop = kzalloc(sizeof(*prop), GFP_KERNEL);
	if (!prop)
		return -ENOMEM;
	prop->name = kstrdup(name, GFP_KERNEL);
	p = kzalloc(sizeof(*p), GFP_KERNEL);
	if (!p) { kfree(prop); return -ENOMEM; }
	*p = cpu_to_be64(val);
	prop->value = p;
	prop->length = sizeof(*p);
	return of_changeset_add_property(&ocs, np, prop);
}

static void attach_prop(struct device_node *np, struct property *prop)
{
	prop->next = np->properties;
	np->properties = prop;
}

static struct device_node *add_child(struct device_node *parent,
				     const char *name, phandle ph)
{
	struct device_node *np;

	np = kzalloc(sizeof(*np), GFP_KERNEL);
	if (!np)
		return NULL;
	np->full_name = kstrdup(name, GFP_KERNEL);
	np->parent = parent;
	kobject_init(&np->kobj, dn_ktype);
	fwnode_init(&np->fwnode, &of_fwnode_ops);
	of_node_set_flag(np, OF_DYNAMIC);
	of_node_set_flag(np, OF_DETACHED);

	if (ph) {
		struct property *pp;
		__be32 *p;

		pp = kzalloc(sizeof(*pp), GFP_KERNEL);
		p = kzalloc(sizeof(*p), GFP_KERNEL);
		if (pp && p) {
			pp->name = kstrdup("phandle", GFP_KERNEL);
			*p = cpu_to_be32(ph);
			pp->value = p;
			pp->length = sizeof(*p);
			attach_prop(np, pp);
		}
	}

	if (of_changeset_attach_node(&ocs, np)) {
		pr_err("dragon-camera: attach_node %s failed\n", name);
		kfree(np->full_name);
		kfree(np);
		return NULL;
	}
	return np;
}

static struct device_node *find_camss_port(struct device_node *camss, u32 reg)
{
	struct device_node *ports, *child, *found = NULL;

	ports = of_get_child_by_name(camss, "ports");
	if (!ports)
		return NULL;
	for_each_child_of_node(ports, child) {
		u32 r;
		if (!of_property_read_u32(child, "reg", &r) && r == reg) {
			found = child;
			break;
		}
	}
	of_node_put(ports);
	return found;
}

static int add_sensor(struct device_node *cci_bus, struct device_node *camss,
		      u32 camss_port_reg, u32 mclk_id,
		      phandle cam_ep_ph, phandle camss_ep_ph)
{
	struct device_node *cam, *port, *ep, *camss_port, *camss_ep;
	u32 data_lanes[] = {1, 2};
	u32 cam_clocks[] = {PHANDLE_CAMCC, mclk_id};
	u32 csi_data_lanes[] = {0, 1};
	int ret;

	cam = add_child(cci_bus, "camera@10", 0);
	if (!cam) return -ENOMEM;
	ret = add_prop_string(cam, "compatible", "sony,imx219");
	if (ret) return ret;
	add_prop_u32(cam, "reg", 0x10);
	add_prop_u32_array(cam, "clocks", cam_clocks, 2);
	add_prop_u32_array(cam, "assigned-clocks", cam_clocks, 2);
	add_prop_u32(cam, "assigned-clock-rates", 24000000);

	port = add_child(cam, "port", 0);
	if (!port) return -ENOMEM;

	ep = add_child(port, "endpoint", cam_ep_ph);
	if (!ep) return -ENOMEM;
	add_prop_u32_array(ep, "data-lanes", data_lanes, 2);
	add_prop_u64(ep, "link-frequencies", 456000000ULL);
	add_prop_u32(ep, "remote-endpoint", camss_ep_ph);

	camss_port = find_camss_port(camss, camss_port_reg);
	if (!camss_port) {
		pr_err("dragon-camera: CAMSS port@%u not found\n",
		       camss_port_reg);
		return -ENODEV;
	}

	camss_ep = add_child(camss_port, "endpoint", camss_ep_ph);
	if (!camss_ep) return -ENOMEM;
	add_prop_u32_array(camss_ep, "data-lanes", csi_data_lanes, 2);
	add_prop_u32(camss_ep, "clock-lanes", 7);
	add_prop_u32(camss_ep, "remote-endpoint", cam_ep_ph);

	return 0;
}

static int __init camera_overlay_init(void)
{
	struct device_node *camss, *cci1, *cci2, *bus;
	struct device_node *root;
	int ret;

	root = of_find_node_by_path("/");
	if (!root)
		return -ENODEV;
	dn_ktype = root->kobj.ktype;
	of_node_put(root);

	camss = of_find_node_by_path("/soc@0/isp@acb3000");
	cci1 = of_find_node_by_path("/soc@0/cci@ac4b000");
	cci2 = of_find_node_by_path("/soc@0/cci@ac4a000");
	if (!camss || !cci1 || !cci2) {
		pr_err("dragon-camera: CAMSS/CCI1/CCI2 not found\n");
		of_node_put(camss);
		of_node_put(cci1);
		of_node_put(cci2);
		return -ENODEV;
	}

	if (of_device_is_available(camss)) {
		pr_info("dragon-camera: CAMSS already enabled\n");
		of_node_put(camss);
		of_node_put(cci1);
		of_node_put(cci2);
		return 0;
	}

	pr_info("dragon-camera: enabling CAMSS + 2x IMX219...\n");
	of_changeset_init(&ocs);

	/* Enable CCI buses first (no endpoint parsing during probe) */
	ret = set_prop_string(cci1, "status", "okay");
	if (ret) goto err;
	ret = set_prop_string(cci2, "status", "okay");
	if (ret) goto err;

	/* CAM2: IMX219 on CCI1/i2c-bus@0, CAMSS port@2, MCLK2 */
	bus = of_find_node_by_path("/soc@0/cci@ac4b000/i2c-bus@0");
	if (!bus) {
		pr_err("dragon-camera: CCI1 i2c-bus@0 not found\n");
		ret = -ENODEV;
		goto err;
	}
	pr_info("dragon-camera: adding CAM2 (CCI1, port@2, MCLK2)...\n");
	ret = add_sensor(bus, camss, 2, CAM_CC_MCLK2_CLK,
			 PHANDLE_CAM2_EP, PHANDLE_CAMSS_PORT2_EP);
	if (ret) goto err;

	/* CAM3: IMX219 on CCI1/i2c-bus@1 (bus 20), CAMSS port@3, MCLK3 */
	bus = of_find_node_by_path("/soc@0/cci@ac4b000/i2c-bus@1");
	if (!bus) {
		pr_err("dragon-camera: CCI1 i2c-bus@1 not found\n");
		ret = -ENODEV;
		goto err;
	}
	pr_info("dragon-camera: adding CAM3 (CCI1/bus@1, port@3, MCLK3)...\n");
	ret = add_sensor(bus, camss, 3, CAM_CC_MCLK3_CLK,
			 PHANDLE_CAM3_EP, PHANDLE_CAMSS_PORT3_EP);
	if (ret) goto err;

	/* Enable CAMSS AFTER endpoints are added so probe finds them */
	ret = set_prop_string(camss, "status", "okay");
	if (ret) goto err;

	pr_info("dragon-camera: applying changeset...\n");
	ret = of_changeset_apply(&ocs);
	if (ret) {
		pr_err("dragon-camera: changeset apply failed: %d\n", ret);
		goto err;
	}

	applied = true;
	pr_info("dragon-camera: changeset applied OK\n");

	/* De-assert sensor power-down (TLMM GPIO 77 active-high) */
	{
		struct gpio_device *gdev;
		struct gpio_chip *chip;

		gdev = gpio_device_find_by_label("f100000.pinctrl");
		if (gdev) {
			chip = gpio_device_get_chip(gdev);
			if (chip) {
				cam_pwdn_desc = gpiochip_request_own_desc(
					chip, CAM_PWDN_GPIO,
					"cam_pwdn", GPIO_LOOKUP_FLAGS_DEFAULT,
					GPIOD_OUT_HIGH);
				if (IS_ERR(cam_pwdn_desc)) {
					pr_warn("dragon-camera: gpio %d: %ld\n",
						CAM_PWDN_GPIO,
						PTR_ERR(cam_pwdn_desc));
					cam_pwdn_desc = NULL;
				}
			}
			gpio_device_put(gdev);
		}
	}

	pr_info("dragon-camera: populating CCI1...\n");
	of_platform_populate(cci1, NULL, NULL, NULL);
	pr_info("dragon-camera: populating CCI2...\n");
	of_platform_populate(cci2, NULL, NULL, NULL);

	pr_info("dragon-camera: 2x IMX219 enabled (CAM2+CAM3)\n");
	of_node_put(camss);
	of_node_put(cci1);
	of_node_put(cci2);
	return 0;

err:
	of_changeset_destroy(&ocs);
	of_node_put(camss);
	of_node_put(cci1);
	of_node_put(cci2);
	return ret;
}

static void __exit camera_overlay_exit(void)
{
	if (applied) {
		if (cam_pwdn_desc) {
			gpiod_set_value(cam_pwdn_desc, 0);
			gpiochip_free_own_desc(cam_pwdn_desc);
		}
		of_changeset_revert(&ocs);
	}
	of_changeset_destroy(&ocs);
}

module_init(camera_overlay_init);
module_exit(camera_overlay_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Dragon Q6A dual camera DT fixup");
