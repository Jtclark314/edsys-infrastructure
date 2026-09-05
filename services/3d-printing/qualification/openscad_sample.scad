// Editable parameterized sample. Millimeters. Not approved for printing.
length = 30;
width = 20;
height = 10;
hole_diameter = 4;
$fn = 64;
difference() {
    cube([length, width, height], center=true);
    cylinder(h=height+2, d=hole_diameter, center=true);
}
